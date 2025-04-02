bl_info = {
    "name": "TRELLIS 3D Generation",
    "author": "FishWoWater",
    "version": (0, 2),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > TRELLIS",
    "description": "3D Mesh Generation with TRELLIS (Image-to-3D and Text-to-3D)",
    "category": "3D View",
}

import bpy
import os
import requests
import tempfile
import base64
import socket
import json
import traceback 
from bpy.props import StringProperty, BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy.utils import register_class, unregister_class

# Configuration
CACHE_DIR = os.path.join(tempfile.gettempdir(), "trellis_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def get_cache_path(file_url):
    """Get local cache path for a file URL"""
    return os.path.join(CACHE_DIR, file_url.split('/')[-2] + '_' + file_url.split('/')[-1])


def is_cached(file_url):
    """Check if file is already in cache"""
    cache_path = get_cache_path(file_url)
    return os.path.exists(cache_path)


def download_file(file_url):
    """Download file if not in cache"""
    cache_path = get_cache_path(file_url)
    if not is_cached(file_url):
        response = requests.get(file_url, timeout=2)
        response.raise_for_status()
        with open(cache_path, 'wb') as f:
            f.write(response.content)
    return cache_path


class TrellisProperties(PropertyGroup):
    api_url: StringProperty(name="Endpoint Url",
                            description="TRELLIS API URL",
                            default="http://localhost:6006",
                            maxlen=1024)
    server_status: StringProperty(name="Server Status", default="unknown")
    # Image-to-3D properties
    image_path: StringProperty(name="input image path",
                               description="Path to the image file",
                               default="",
                               subtype='FILE_PATH')
    # Text-to-3D properties
    prompt_text: StringProperty(name="Text Prompt",
                              description="Text description for 3D generation",
                              default="",
                              maxlen=128)
    negative_prompt_text: StringProperty(name="Negative Text Prompt",
                              description="Negative Text description for 3D generation",
                              default="",
                              maxlen=128)

    # Common properties for both modes
    sparse_structure_sample_steps: IntProperty(name="1st stage sample steps",
                                               description="Number of sampling steps for the (SparseStructure)coarse geoemtry generation",
                                               default=12,
                                               min=1)
    sparse_structure_cfg_strength: FloatProperty(name="1st stage cfg strength",
                                                 description="CFG strength for (SparseStructure)coarse geometry generation",
                                                 default=7.5,
                                                 min=0.0)
    slat_sample_steps: IntProperty(name="2nd stage sample steps",
                                   description="Number of sampling steps for (SLAT)final geometry and texture generation",
                                   default=12,
                                   min=1)
    slat_cfg_strength: FloatProperty(name="2nd stage cfg strength",
                                     description="CFG strength for (SLAT)final geometry and texture generation",
                                     default=3.5,
                                     min=0.0)
    simplify_ratio: FloatProperty(name="simplify ratio",
                                  description="Ratio of triangles to remove in simplification",
                                  default=0.95,
                                  min=0.0,
                                  max=1.0)
    texture_size: IntProperty(name="texture size", description="Size of the texture used for GLB", default=1024, min=64)
    texture_bake_mode: EnumProperty(name="Tex Bake",
                                    description="Mode for texture baking",
                                    items=[('opt', "optimized", "Optimized texture baking"),
                                           ('fast', "fast", "Fast texture baking")],
                                    default='fast')
    auto_refresh: BoolProperty(
        name="Auto Refresh",
        description="Automatically refresh request status",
        default=True,
        update=lambda self, context: start_auto_refresh() if self.auto_refresh else stop_auto_refresh()
    )
    task_id: StringProperty(name="Task ID", description="Current task ID for tracking conversion progress", default="")
    show_parameters: BoolProperty(name="Show Parameters", description="Show/hide generation parameters", default=False)
    show_history: BoolProperty(name="Show History", description="Show/hide history section", default=False)
    active_tab: EnumProperty(
        name="Active Tab",
        description="Active generation tab",
        items=[
            ('IMAGE_TO_3D', "Image to 3D", "Generate 3D models from images"),
            ('TEXT_TO_3D', "Text to 3D", "Generate 3D models from text descriptions")
        ],
        default='IMAGE_TO_3D'
    )



class TRELLIS_OT_convert_image(Operator):
    bl_idname = "trellis.convert_image"
    bl_label = "Generation"
    bl_description = "Convert image to 3D model using TRELLIS"

    def execute(self, context):
        props = context.scene.trellis_props

        if not props.image_path:
            self.report({'ERROR'}, "Please select an image file")
            return {'CANCELLED'}

        try:
            with open(props.image_path, 'rb') as f:
                # Read and encode the image file as base64
                image_data = base64.b64encode(f.read()).decode('utf-8')
                
                data = {
                    'image_data': image_data,
                    'image_name': os.path.splitext(os.path.basename(props.image_path))[0],
                    'sparse_structure_sample_steps': props.sparse_structure_sample_steps,
                    'sparse_structure_cfg_strength': props.sparse_structure_cfg_strength,
                    'slat_sample_steps': props.slat_sample_steps,
                    'slat_cfg_strength': props.slat_cfg_strength,
                    'simplify_ratio': props.simplify_ratio,
                    'texture_size': props.texture_size,
                    'texture_bake_mode': props.texture_bake_mode
                }
                
                headers = {'Content-Type': 'application/json'}
                response = requests.post(f"{props.api_url}/image_to_3d", json=data, headers=headers, timeout=2)
                response.raise_for_status()
                result = response.json()

                if result['status'] == 'queued':
                    self.report({'INFO'}, f"Request queued with ID: {result['request_id']}")
                    # Force an immediate refresh and update the UI
                    bpy.ops.trellis.refresh_status()
                    # TODO: CHECK THIS
                    # Force the panel to redraw
                    for area in context.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                    return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}


class TRELLIS_OT_import_result(Operator):
    bl_idname = "trellis.import_result"
    bl_label = "Import Result"
    bl_description = "Import the selected result into Blender"

    file_url: StringProperty()

    def execute(self, context):
        try:
            # Download and import the file
            file_path = download_file(self.file_url)
            bpy.ops.import_scene.gltf(filepath=file_path)
            self.report({'INFO'}, "Model imported successfully")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error importing model: {str(e)}")
            return {'CANCELLED'}


class TRELLIS_OT_refresh_status(Operator):
    bl_idname = "trellis.refresh_status"
    bl_label = "Refresh Status"
    bl_description = "Refresh the status of recent requests"

    def execute(self, context):
        try:
            # Get recent requests
            response = requests.get(f"{context.scene.trellis_props.api_url}/my_requests", timeout=1)
            response.raise_for_status()
            
            # Format the finish_time for each request if available
            result = response.json()
            for req in result.get('requests', []):
                if 'finish_time' in req and req['finish_time']:
                    # Convert ISO format to more readable format
                    try:
                        from datetime import datetime
                        # Handle ISO format without Z suffix
                        finish_time = datetime.fromisoformat(req['finish_time'])
                        # Format with date and time, showing only hours and minutes
                        req['display_time'] = finish_time.strftime('%Y-%m-%d %H:%M')
                    except Exception as e:
                        print(f"Error parsing time: {e}")
                        # Keep original if parsing fails
                        req['display_time'] = req['finish_time']
                        
            context.scene['trellis_requests'] = result
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error refreshing status: {str(e)}")
            return {'CANCELLED'}


class TRELLIS_OT_convert_text(Operator):
    bl_idname = "trellis.convert_text"
    bl_label = "Text to 3D Generation"
    bl_description = "Convert text prompt to 3D model using TRELLIS"

    def execute(self, context):
        props = context.scene.trellis_props

        if not props.prompt_text.strip():
            self.report({'ERROR'}, "Please enter a text prompt")
            return {'CANCELLED'}

        try:
            data = {
                'text': props.prompt_text,
                'negative_text': props.negative_prompt_text,
                'ss_sample_steps': props.sparse_structure_sample_steps,
                'ss_cfg_strength': props.sparse_structure_cfg_strength,
                'slat_sample_steps': props.slat_sample_steps,
                'slat_cfg_strength': props.slat_cfg_strength,
                'simplify_ratio': props.simplify_ratio,
                'texture_size': props.texture_size,
                'texture_bake_mode': props.texture_bake_mode
            }
            
            headers = {'Content-Type': 'application/json'}
            response = requests.post(f"{props.api_url}/text_to_3d", json=data, headers=headers)
            response.raise_for_status()
            result = response.json()

            if result['status'] == 'queued':
                self.report({'INFO'}, f"Request queued with ID: {result['request_id']}")
                # Force an immediate refresh and update the UI
                bpy.ops.trellis.refresh_status()
                # Force the panel to redraw
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
                return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}


class TRELLIS_OT_show_preview(Operator):
    bl_idname = "trellis.show_preview"
    bl_label = "Preview Image"
    bl_description = "Show preview of selected image"

    def execute(self, context):
        props = context.scene.trellis_props
        if not props.image_path or not os.path.exists(props.image_path):
            self.report({'ERROR'}, "Please select a valid image file")
            return {'CANCELLED'}

        # Load image into Blender
        image_name = os.path.basename(props.image_path)
        if image_name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[image_name])
        img = bpy.data.images.load(props.image_path)

        # Try to find an existing image editor
        image_editor = None
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    image_editor = area
                    break
            if image_editor:
                break

        if not image_editor:
            # If no image editor exists, create one by splitting the 3D view
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        # Store current context
                        temp_override = context.copy()
                        temp_override['window'] = window
                        temp_override['screen'] = window.screen
                        temp_override['area'] = area
                        temp_override['region'] = area.regions[-1]

                        # Split the area
                        with context.temp_override(**temp_override):
                            bpy.ops.screen.area_split(direction='VERTICAL', factor=0.3)

                        # The new area is the last one in the areas list
                        new_area = window.screen.areas[-1]
                        new_area.type = 'IMAGE_EDITOR'
                        image_editor = new_area
                        break
                if image_editor:
                    break

        # Set the image in the editor
        if image_editor:
            image_editor.spaces.active.image = img
        else:
            self.report({'WARNING'}, "Could not create image editor, but image was loaded")

        return {'FINISHED'}


class TRELLIS_OT_convert_mesh(Operator):
    bl_idname = "trellis.convert_mesh"
    bl_label = "Convert Selected to GLB"
    bl_description = "Convert selected object to GLB and process with TRELLIS using image conditioning"

    def execute(self, context):
        props = context.scene.trellis_props

        # Check requirements
        if not context.active_object:
            self.report({'ERROR'}, "Please select an object to convert")
            return {'CANCELLED'}
        if not props.image_path:
            self.report({'ERROR'}, "Please select an input image")
            return {'CANCELLED'}

        # Create a temporary directory for the GLB
        temp_dir = tempfile.mkdtemp()
        temp_glb = os.path.join(temp_dir, "temp.glb")

        try:
            # Export selected object to GLB
            bpy.ops.export_scene.gltf(
                filepath=temp_glb,
                use_selection=True,
                export_format='GLB',
                export_yup=False  # This ensures Z-up orientation
            )

            # Read and encode both GLB and image files as base64
            with open(temp_glb, 'rb') as glb_file, open(props.image_path, 'rb') as img_file:
                glb_data = base64.b64encode(glb_file.read()).decode('utf-8')
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
                
                data = {
                    'mesh_data': glb_data,
                    'image_data': img_data,
                    'image_name': os.path.splitext(os.path.basename(props.image_path))[0],
                    'sparse_structure_sample_steps': props.sparse_structure_sample_steps,
                    'sparse_structure_cfg_strength': props.sparse_structure_cfg_strength,
                    'slat_sample_steps': props.slat_sample_steps,
                    'slat_cfg_strength': props.slat_cfg_strength,
                    'simplify_ratio': props.simplify_ratio,
                    'texture_size': props.texture_size,
                    'texture_bake_mode': props.texture_bake_mode,
                    'is_dv_mode': True
                }
                
                headers = {'Content-Type': 'application/json'}
                response = requests.post(f"{props.api_url}/image_to_3d", json=data, headers=headers)
                response.raise_for_status()
                result = response.json()

                if result['status'] == 'queued':
                    # Refresh status immediately to show the new request
                    bpy.ops.trellis.refresh_status()
                    self.report({'INFO'}, f"Request queued with ID: {result.get('request_id', '')}")
                    return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}
        finally:
            # Clean up temporary files
            if os.path.exists(temp_glb):
                os.remove(temp_glb)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)

        return {'FINISHED'}


class TRELLIS_OT_convert_text_mesh(Operator):
    bl_idname = "trellis.convert_text_mesh"
    bl_label = "Convert Selected to GLB with Text"
    bl_description = "Convert selected object to GLB and process with TRELLIS using text conditioning"

    def execute(self, context):
        props = context.scene.trellis_props

        # Check requirements
        if not context.active_object:
            self.report({'ERROR'}, "Please select an object to convert")
            return {'CANCELLED'}
        if not props.prompt_text.strip():
            self.report({'ERROR'}, "Please enter a text prompt")
            return {'CANCELLED'}

        # Create a temporary directory for the GLB
        temp_dir = tempfile.mkdtemp()
        temp_glb = os.path.join(temp_dir, "temp.glb")

        try:
            # Export selected object to GLB
            bpy.ops.export_scene.gltf(
                filepath=temp_glb,
                use_selection=True,
                export_format='GLB',
                export_yup=False  # This ensures Z-up orientation
            )

            # Read and encode GLB file as base64
            with open(temp_glb, 'rb') as glb_file:
                glb_data = base64.b64encode(glb_file.read()).decode('utf-8')
                
                data = {
                    'mesh_data': glb_data,
                    'text': props.prompt_text,
                    'negative_text': props.negative_prompt_text,
                    'ss_sample_steps': props.sparse_structure_sample_steps,
                    'ss_cfg_strength': props.sparse_structure_cfg_strength,
                    'slat_sample_steps': props.slat_sample_steps,
                    'slat_cfg_strength': props.slat_cfg_strength,
                    'simplify_ratio': props.simplify_ratio,
                    'texture_size': props.texture_size,
                    'texture_bake_mode': props.texture_bake_mode,
                    'is_dv_mode': True
                }
                
                headers = {'Content-Type': 'application/json'}
                response = requests.post(f"{props.api_url}/text_to_3d", json=data, headers=headers)
                response.raise_for_status()
                result = response.json()

                if result['status'] == 'queued':
                    # Refresh status immediately to show the new request
                    bpy.ops.trellis.refresh_status()
                    self.report({'INFO'}, f"Request queued with ID: {result.get('request_id', '')}")
                    return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}
        finally:
            # Clean up temporary files
            if os.path.exists(temp_glb):
                os.remove(temp_glb)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)

        return {'FINISHED'}


class TRELLIS_OT_check_server(Operator):
    bl_idname = "trellis.check_server"
    bl_label = "Check Server"
    bl_description = "Check if the TRELLIS server is running"

    def execute(self, context):
        props = context.scene.trellis_props
        try:
            response = requests.get(f"{props.api_url}/status", timeout=2)
            if response.status_code == 200 and response.json().get('status') == 'ok':
                props.server_status = "online"
                self.report({'INFO'}, "TRELLIS server is online")
            else:
                props.server_status = "offline"
                self.report({'ERROR'}, "TRELLIS server is not responding correctly")
        except Exception as e:
            props.server_status = "offline"
            self.report({'ERROR'}, f"Error connecting to server: {str(e)}")
        
        # Force redraw of the UI
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
                
        return {'FINISHED'}


class TRELLIS_PT_main_panel(Panel):
    bl_label = "TRELLIS 3D Generation"
    bl_idname = "TRELLIS_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'TRELLIS'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.trellis_props
        scene = context.scene

        # API Configuration
        box = layout.box()
        box.label(text="API Configuration")
        row = box.row()
        row.prop(props, "api_url")
        row = box.row()
        row.operator("trellis.check_server", text="Check Connection", icon='FILE_REFRESH')
        
        # MCP Server section
        mcp_box = layout.box()
        row = mcp_box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="MCP Connections")
        row = mcp_box.row()
        row.prop(scene, "trellis_mcp_port")
        row = mcp_box.row()
        row.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")
        row = mcp_box.row()
        row.enabled = True
        row.prop(scene, "MCP_use_trellis", text="Use Trellis as assets generator")
        if not scene.trellis_mcp_server_running:
            mcp_box.operator("trellis.start_mcp_server", text="Start MCP Server")
        else:
            mcp_box.operator("trellis.stop_mcp_server", text="Stop MCP Server")
            mcp_box.label(text=f"Running on port {scene.trellis_mcp_port}")

        # Show server status
        if props.server_status == "online":
            box.label(text="Server Status: Online", icon='CHECKMARK')
            
            # Only show the rest of the UI if the server is online
            # Tab selector
            row = layout.row()
            row.prop(props, "active_tab", expand=True)
            
            # Draw the appropriate panel based on active tab
            if props.active_tab == 'IMAGE_TO_3D':
                self.draw_image_to_3d(context, layout)
            else:  # TEXT_TO_3D
                self.draw_text_to_3d(context, layout)
            
            # History section (common for both tabs)
            self.draw_history(context, layout)
        elif props.server_status == "offline":
            box.label(text="Server Status: Offline", icon='CANCEL')
            layout.label(text="Please check the server connection", icon='ERROR')
        else:
            box.label(text="Server Status: Unknown", icon='QUESTION')
            layout.label(text="Please check the server connection", icon='INFO')
    
    def draw_image_to_3d(self, context, layout):
        props = context.scene.trellis_props
        
        # Image selection
        box = layout.box()
        box.label(text="Image Input")
        row = box.row()
        row.prop(props, "image_path")

        # Preview button
        if props.image_path and os.path.exists(props.image_path):
            row = box.row()
            row.operator("trellis.show_preview", text="Preview Image", icon='IMAGE_DATA')

        # Parameters with collapse button
        self.draw_parameters(context, layout)

        # Convert buttons
        layout.operator("trellis.convert_image", text="Image to 3D", icon='MESH_CUBE')
        layout.operator("trellis.convert_mesh", text="Image-Conditioned Detail Variation", icon='MESH_CUBE')
    
    def draw_text_to_3d(self, context, layout):
        props = context.scene.trellis_props
        
        # Text input
        box = layout.box()
        box.label(text="Text Prompt")
        col = box.column()
        col.prop(props, "prompt_text", text="")
        
        # Negative prompt
        neg_box = layout.box()
        neg_box.label(text="Negative Text Prompt")
        neg_col = neg_box.column()
        neg_col.prop(props, "negative_prompt_text", text="")
        
        # Parameters with collapse button
        self.draw_parameters(context, layout)
        
        # Convert buttons
        layout.operator("trellis.convert_text", text="Text to 3D", icon='MESH_CUBE')
        layout.operator("trellis.convert_text_mesh", text="Text-Conditioned Detail Variation", icon='MESH_CUBE')
    
    def draw_parameters(self, context, layout):
        props = context.scene.trellis_props
        
        params_box = layout.box()
        row = params_box.row()
        row.prop(props,
                 "show_parameters",
                 text="Parameters",
                 icon='TRIA_DOWN' if props.show_parameters else 'TRIA_RIGHT',
                 emboss=False)

        if props.show_parameters:
            col = params_box.column(align=True)
            col.prop(props, "sparse_structure_sample_steps")
            col.prop(props, "sparse_structure_cfg_strength")
            col.prop(props, "slat_sample_steps")
            col.prop(props, "slat_cfg_strength")
            col.prop(props, "simplify_ratio")
            col.prop(props, "texture_size")
            col.prop(props, "texture_bake_mode")
    
    def draw_history(self, context, layout):
        props = context.scene.trellis_props
        
        # History section in a single box
        history_box = layout.box()
        row = history_box.row()
        row.prop(props,
                 "show_history",
                 text="History",
                 icon='TRIA_DOWN' if props.show_history else 'TRIA_RIGHT',
                 emboss=False)
        row.operator("trellis.refresh_status", text="", icon='FILE_REFRESH')

        if props.show_history:
            row = history_box.row()
            row.prop(props, "auto_refresh")
            
            if 'trellis_requests' in context.scene:
                requests = context.scene['trellis_requests'].get('requests', [])
                for req in requests:
                    row = history_box.row(align=True)
                    # Show request type and ID
                    task_type = "TextTo3D" if req.get('task_type', '') == 'text_to_3d' else "ImageTo3D"
                    instance_name = req.get('image_name', '') if task_type == "ImageTo3D" else req.get('text', '')
                    display_name = f"{task_type}: {instance_name[:8]}(ID-{req['request_id'][:8]})"
                    row.label(text=display_name)
                    if 'display_time' in req:
                        row.label(text=req['display_time'])
                    row.label(text=req['status'])

                    # Show finish time if available
                    # if 'display_time' in req:
                    #     time_row = history_box.row()
                    #     time_row.label(text=f"    Finished: {req['display_time']}")

                    if req['status'] == 'complete' and req.get('output_files'):
                        for file_url in req['output_files']:
                            if file_url.endswith('.glb'):
                                op = row.operator("trellis.import_result", text="", icon='IMPORT')
                                op.file_url = file_url


def auto_refresh_callback():
    try:
        if not bpy.context or not hasattr(bpy.context.scene, 'trellis_props'):
            return None  # Stop timer if context is invalid
        
        if bpy.context.scene.trellis_props.auto_refresh:
            try:
                bpy.ops.trellis.refresh_status()
            except Exception as e:
                print(f"Error in auto refresh: {str(e)}")
            return 3.0  # Return time until next execution
        return None  # Stop timer if auto_refresh is disabled
    except Exception:
        return None  # Stop timer if any error occurs

def start_auto_refresh():
    if not bpy.app.timers.is_registered(auto_refresh_callback):
        bpy.app.timers.register(auto_refresh_callback, persistent=True)

def stop_auto_refresh():
    if bpy.app.timers.is_registered(auto_refresh_callback):
        bpy.app.timers.unregister(auto_refresh_callback)

# MCP Server implementation
class BlenderMCPServer:
    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.client = None
        self.command_queue = []
        self.buffer = b""  # Buffer for incomplete data

    def start(self):
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.setblocking(False)
            # Register the timer
            bpy.app.timers.register(self._process_server, persistent=True)
            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False
        if hasattr(bpy.app.timers, "unregister"):
            if bpy.app.timers.is_registered(self._process_server):
                bpy.app.timers.unregister(self._process_server)
        if self.socket:
            self.socket.close()
        if self.client:
            self.client.close()
        self.socket = None
        self.client = None
        print("BlenderMCP server stopped")

    def _process_server(self):
        """Timer callback to process server operations"""
        if not self.running:
            return None  # Unregister timer

        try:
            # Accept new connections
            if not self.client and self.socket:
                try:
                    self.client, address = self.socket.accept()
                    self.client.setblocking(False)
                    print(f"Connected to client: {address}")
                except BlockingIOError:
                    pass  # No connection waiting
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")

            # Process existing connection
            if self.client:
                try:
                    # Try to receive data
                    try:
                        data = self.client.recv(8192)
                        if data:
                            self.buffer += data
                            # Try to process complete messages
                            try:
                                # Attempt to parse the buffer as JSON
                                command = json.loads(self.buffer.decode("utf-8"))
                                # If successful, clear the buffer and process command
                                self.buffer = b""
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                self.client.sendall(response_json.encode("utf-8"))
                            except json.JSONDecodeError:
                                # Incomplete data, keep in buffer
                                pass
                        else:
                            # Connection closed by client
                            print("Client disconnected")
                            self.client.close()
                            self.client = None
                            self.buffer = b""
                    except BlockingIOError:
                        pass  # No data available
                    except Exception as e:
                        print(f"Error receiving data: {str(e)}")
                        self.client.close()
                        self.client = None
                        self.buffer = b""

                except Exception as e:
                    print(f"Error with client: {str(e)}")
                    if self.client:
                        self.client.close()
                        self.client = None
                    self.buffer = b""

        except Exception as e:
            print(f"Server error: {str(e)}")

        return 0.1  # Check again in 0.1 seconds

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            # Ensure we're in the right context
            if cmd_type in ["create_object", "modify_object", "delete_object"]:
                override = bpy.context.copy()
                override["area"] = [
                    area for area in bpy.context.screen.areas if area.type == "VIEW_3D"
                ][0]
                with bpy.context.temp_override(**override):
                    return self._execute_command_internal(command)
            else:
                return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")

            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Add a handler for checking PolyHaven status
        if cmd_type == "get_polyhaven_status":
            return {"status": "success", "result": self.get_polyhaven_status()}

        # Base handlers that are always available
        handlers = {
            "get_scene_info": self.get_scene_info,
            "create_object": self.create_object,
            "modify_object": self.modify_object,
            "delete_object": self.delete_object,
            "get_object_info": self.get_object_info,
            "execute_code": self.execute_code,
            "set_material": self.set_material,
            "get_polyhaven_status": self.get_polyhaven_status,
            "import_trellis_glb_model": self.import_trellis_glb_model,
        }

        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
                "set_texture": self.set_texture,
            }
            handlers.update(polyhaven_handlers)

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    def import_trellis_glb_model(self, url):
        response = requests.get(url, timeout=1)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".glb")
        temp_file.write(response.content)
        temp_file.close()

        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.import_scene.gltf(filepath=temp_file.name)
        imported_objects = bpy.context.selected_objects
        model_info = []

        for obj in imported_objects:
            # calculate the bounding box 
            bbox_dimensions = [
                dim * scale for dim, scale in zip(obj.dimensions, obj.scale)
            ]

            model_info.append(
                {
                    "name": obj.name,
                    "dimensions": {
                        "x": round(bbox_dimensions[0], 4),
                        "y": round(bbox_dimensions[1], 4),
                        "z": round(bbox_dimensions[2], 4),
                    },
                }
            )

        os.unlink(temp_file.name)

        return {
            "status": "success",
            "message": "Model imported successfully",
            "models": model_info,  
        }

    def get_simple_info(self):
        """Get basic Blender information"""
        return {
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "scene_name": bpy.context.scene.name,
            "object_count": len(bpy.context.scene.objects),
        }

    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # Reduced from 20 to 10
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [
                        round(float(obj.location.x), 2),
                        round(float(obj.location.y), 2),
                        round(float(obj.location.z), 2),
                    ],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def create_object(
        self,
        type="CUBE",
        name=None,
        location=(0, 0, 0),
        rotation=(0, 0, 0),
        scale=(1, 1, 1),
    ):
        """Create a new object in the scene"""
        # Deselect all objects
        bpy.ops.object.select_all(action="DESELECT")

        # Create the object based on type
        if type == "CUBE":
            bpy.ops.mesh.primitive_cube_add(
                location=location, rotation=rotation, scale=scale
            )
        elif type == "SPHERE":
            bpy.ops.mesh.primitive_uv_sphere_add(
                location=location, rotation=rotation, scale=scale
            )
        elif type == "CYLINDER":
            bpy.ops.mesh.primitive_cylinder_add(
                location=location, rotation=rotation, scale=scale
            )
        elif type == "PLANE":
            bpy.ops.mesh.primitive_plane_add(
                location=location, rotation=rotation, scale=scale
            )
        elif type == "CONE":
            bpy.ops.mesh.primitive_cone_add(
                location=location, rotation=rotation, scale=scale
            )
        elif type == "TORUS":
            bpy.ops.mesh.primitive_torus_add(
                location=location, rotation=rotation
            )
        elif type == "EMPTY":
            bpy.ops.object.empty_add(location=location, rotation=rotation, scale=scale)
        elif type == "CAMERA":
            bpy.ops.object.camera_add(location=location, rotation=rotation)
        elif type == "LIGHT":
            bpy.ops.object.light_add(
                type="POINT", location=location, rotation=rotation, scale=scale
            )
        else:
            raise ValueError(f"Unsupported object type: {type}")

        # Get the created object
        obj = bpy.context.active_object

        # Rename if name is provided
        if name:
            obj.name = name

        return {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
        }

    def modify_object(
        self, name, location=None, rotation=None, scale=None, visible=None
    ):
        """Modify an existing object in the scene"""
        # Find the object by name
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Modify properties as requested
        if location is not None:
            obj.location = location

        if rotation is not None:
            obj.rotation_euler = rotation

        if scale is not None:
            obj.scale = scale

        if visible is not None:
            obj.hide_viewport = not visible
            obj.hide_render = not visible

        return {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
        }

    def delete_object(self, name):
        """Delete an object from the scene"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Store the name to return
        obj_name = obj.name

        # Select and delete the object
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.ops.object.delete()

        return {"deleted": obj_name}

    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == "MESH" and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}
            exec(code, namespace)
            return {"executed": True}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")

    def set_material(
        self, object_name, material_name=None, create_if_missing=True, color=None
    ):
        """Set or create a material for an object"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")

            # Make sure object can accept materials
            if not hasattr(obj, "data") or not hasattr(obj.data, "materials"):
                raise ValueError(f"Object {object_name} cannot accept materials")

            # Create or get material
            if material_name:
                mat = bpy.data.materials.get(material_name)
                if not mat and create_if_missing:
                    mat = bpy.data.materials.new(name=material_name)
                    print(f"Created new material: {material_name}")
            else:
                # Generate unique material name if none provided
                mat_name = f"{object_name}_material"
                mat = bpy.data.materials.get(mat_name)
                if not mat:
                    mat = bpy.data.materials.new(name=mat_name)
                material_name = mat_name
                print(f"Using material: {mat_name}")

            # Set up material nodes if needed
            if mat:
                if not mat.use_nodes:
                    mat.use_nodes = True

                # Get or create Principled BSDF
                principled = mat.node_tree.nodes.get("Principled BSDF")
                if not principled:
                    principled = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
                    # Get or create Material Output
                    output = mat.node_tree.nodes.get("Material Output")
                    if not output:
                        output = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
                    # Link if not already linked
                    if not principled.outputs[0].links:
                        mat.node_tree.links.new(principled.outputs[0], output.inputs[0])

                # Set color if provided
                if color and len(color) >= 3:
                    principled.inputs["Base Color"].default_value = (
                        color[0],
                        color[1],
                        color[2],
                        1.0 if len(color) < 4 else color[3],
                    )
                    print(f"Set material color to {color}")

            # Assign material to object if not already assigned
            if mat:
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    # Only modify first material slot
                    obj.data.materials[0] = mat

                print(f"Assigned material {mat.name} to object {object_name}")

                return {
                    "status": "success",
                    "object": object_name,
                    "material": mat.name,
                    "color": color if color else None,
                }
            else:
                raise ValueError(f"Failed to create or find material: {material_name}")

        except Exception as e:
            print(f"Error in set_material: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "object": object_name,
                "material": material_name if "material_name" in locals() else None,
            }

    def render_scene(self, output_path=None, resolution_x=None, resolution_y=None):
        """Render the current scene"""
        if resolution_x is not None:
            bpy.context.scene.render.resolution_x = resolution_x

        if resolution_y is not None:
            bpy.context.scene.render.resolution_y = resolution_y

        if output_path:
            bpy.context.scene.render.filepath = output_path

        # Render the scene
        bpy.ops.render.render(write_still=bool(output_path))

        return {
            "rendered": True,
            "output_path": output_path if output_path else "[not saved]",
            "resolution": [
                bpy.context.scene.render.resolution_x,
                bpy.context.scene.render.resolution_y,
            ],
        }

    def get_polyhaven_categories(self, asset_type):
        """Get categories for a specific asset type from Polyhaven"""
        try:
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {
                    "error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"
                }

            response = requests.get(
                f"https://api.polyhaven.com/categories/{asset_type}", timeout=2
            )
            if response.status_code == 200:
                return {"categories": response.json()}
            else:
                return {
                    "error": f"API request failed with status code {response.status_code}"
                }
        except Exception as e:
            return {"error": str(e)}

    def search_polyhaven_assets(self, asset_type=None, categories=None):
        """Search for assets from Polyhaven with optional filtering"""
        try:
            url = "https://api.polyhaven.com/assets"
            params = {}

            if asset_type and asset_type != "all":
                if asset_type not in ["hdris", "textures", "models"]:
                    return {
                        "error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"
                    }
                params["type"] = asset_type

            if categories:
                params["categories"] = categories

            response = requests.get(url, params=params, timeout=2)
            if response.status_code == 200:
                # Limit the response size to avoid overwhelming Blender
                assets = response.json()
                # Return only the first 20 assets to keep response size manageable
                limited_assets = {}
                for i, (key, value) in enumerate(assets.items()):
                    if i >= 20:  # Limit to 20 assets
                        break
                    limited_assets[key] = value

                return {
                    "assets": limited_assets,
                    "total_count": len(assets),
                    "returned_count": len(limited_assets),
                }
            else:
                return {
                    "error": f"API request failed with status code {response.status_code}"
                }
        except Exception as e:
            return {"error": str(e)}

    def download_polyhaven_asset(
        self, asset_id, asset_type, resolution="1k", file_format=None
    ):
        try:
            # First get the files information
            files_response = requests.get(f"https://api.polyhaven.com/files/{asset_id}", timeout=2)
            if files_response.status_code != 200:
                return {
                    "error": f"Failed to get asset files: {files_response.status_code}"
                }

            files_data = files_response.json()

            # Handle different asset types
            if asset_type == "hdris":
                # For HDRIs, download the .hdr or .exr file
                if not file_format:
                    file_format = "hdr"  # Default format for HDRIs

                if (
                    "hdri" in files_data
                    and resolution in files_data["hdri"]
                    and file_format in files_data["hdri"][resolution]
                ):
                    file_info = files_data["hdri"][resolution][file_format]
                    file_url = file_info["url"]

                    # For HDRIs, we need to save to a temporary file first
                    # since Blender can't properly load HDR data directly from memory
                    with tempfile.NamedTemporaryFile(
                        suffix=f".{file_format}", delete=False
                    ) as tmp_file:
                        # Download the file
                        response = requests.get(file_url, timeout=2)
                        if response.status_code != 200:
                            return {
                                "error": f"Failed to download HDRI: {response.status_code}"
                            }

                        tmp_file.write(response.content)
                        tmp_path = tmp_file.name

                    try:
                        # Create a new world if none exists
                        if not bpy.data.worlds:
                            bpy.data.worlds.new("World")

                        world = bpy.data.worlds[0]
                        world.use_nodes = True
                        node_tree = world.node_tree

                        # Clear existing nodes
                        for node in node_tree.nodes:
                            node_tree.nodes.remove(node)

                        # Create nodes
                        tex_coord = node_tree.nodes.new(type="ShaderNodeTexCoord")
                        tex_coord.location = (-800, 0)

                        mapping = node_tree.nodes.new(type="ShaderNodeMapping")
                        mapping.location = (-600, 0)

                        # Load the image from the temporary file
                        env_tex = node_tree.nodes.new(type="ShaderNodeTexEnvironment")
                        env_tex.location = (-400, 0)
                        env_tex.image = bpy.data.images.load(tmp_path)

                        # FIXED: Use a color space that exists in all Blender versions
                        if file_format.lower() == "exr":
                            # Try to use Linear color space for EXR files
                            try:
                                env_tex.image.colorspace_settings.name = "Linear"
                            except:
                                # Fallback to Non-Color if Linear isn't available
                                env_tex.image.colorspace_settings.name = "Non-Color"
                        else:  # hdr
                            # For HDR files, try these options in order
                            for color_space in [
                                "Linear",
                                "Linear Rec.709",
                                "Non-Color",
                            ]:
                                try:
                                    env_tex.image.colorspace_settings.name = color_space
                                    break  # Stop if we successfully set a color space
                                except:
                                    continue

                        background = node_tree.nodes.new(type="ShaderNodeBackground")
                        background.location = (-200, 0)

                        output = node_tree.nodes.new(type="ShaderNodeOutputWorld")
                        output.location = (0, 0)

                        # Connect nodes
                        node_tree.links.new(
                            tex_coord.outputs["Generated"], mapping.inputs["Vector"]
                        )
                        node_tree.links.new(
                            mapping.outputs["Vector"], env_tex.inputs["Vector"]
                        )
                        node_tree.links.new(
                            env_tex.outputs["Color"], background.inputs["Color"]
                        )
                        node_tree.links.new(
                            background.outputs["Background"], output.inputs["Surface"]
                        )

                        # Set as active world
                        bpy.context.scene.world = world

                        # Clean up temporary file
                        try:
                            tempfile._cleanup()  # This will clean up all temporary files
                        except:
                            pass

                        return {
                            "success": True,
                            "message": f"HDRI {asset_id} imported successfully",
                            "image_name": env_tex.image.name,
                        }
                    except Exception as e:
                        return {"error": f"Failed to set up HDRI in Blender: {str(e)}"}
                else:
                    return {
                        "error": f"Requested resolution or format not available for this HDRI"
                    }

            elif asset_type == "textures":
                if not file_format:
                    file_format = "jpg"  # Default format for textures

                downloaded_maps = {}

                try:
                    for map_type in files_data:
                        if map_type not in ["blend", "gltf"]:  # Skip non-texture files
                            if (
                                resolution in files_data[map_type]
                                and file_format in files_data[map_type][resolution]
                            ):
                                file_info = files_data[map_type][resolution][
                                    file_format
                                ]
                                file_url = file_info["url"]

                                # Use NamedTemporaryFile like we do for HDRIs
                                with tempfile.NamedTemporaryFile(
                                    suffix=f".{file_format}", delete=False
                                ) as tmp_file:
                                    # Download the file
                                    response = requests.get(file_url, timeout=2)
                                    if response.status_code == 200:
                                        tmp_file.write(response.content)
                                        tmp_path = tmp_file.name

                                        # Load image from temporary file
                                        image = bpy.data.images.load(tmp_path)
                                        image.name = (
                                            f"{asset_id}_{map_type}.{file_format}"
                                        )

                                        # Pack the image into .blend file
                                        image.pack()

                                        # Set color space based on map type
                                        if map_type in ["color", "diffuse", "albedo"]:
                                            try:
                                                image.colorspace_settings.name = "sRGB"
                                            except:
                                                pass
                                        else:
                                            try:
                                                image.colorspace_settings.name = (
                                                    "Non-Color"
                                                )
                                            except:
                                                pass

                                        downloaded_maps[map_type] = image

                                        # Clean up temporary file
                                        try:
                                            os.unlink(tmp_path)
                                        except:
                                            pass

                    if not downloaded_maps:
                        return {
                            "error": f"No texture maps found for the requested resolution and format"
                        }

                    # Create a new material with the downloaded textures
                    mat = bpy.data.materials.new(name=asset_id)
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links

                    # Clear default nodes
                    for node in nodes:
                        nodes.remove(node)

                    # Create output node
                    output = nodes.new(type="ShaderNodeOutputMaterial")
                    output.location = (300, 0)

                    # Create principled BSDF node
                    principled = nodes.new(type="ShaderNodeBsdfPrincipled")
                    principled.location = (0, 0)
                    links.new(principled.outputs[0], output.inputs[0])

                    # Add texture nodes based on available maps
                    tex_coord = nodes.new(type="ShaderNodeTexCoord")
                    tex_coord.location = (-800, 0)

                    mapping = nodes.new(type="ShaderNodeMapping")
                    mapping.location = (-600, 0)
                    mapping.vector_type = (
                        "TEXTURE"  # Changed from default 'POINT' to 'TEXTURE'
                    )
                    links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

                    # Position offset for texture nodes
                    x_pos = -400
                    y_pos = 300

                    # Connect different texture maps
                    for map_type, image in downloaded_maps.items():
                        tex_node = nodes.new(type="ShaderNodeTexImage")
                        tex_node.location = (x_pos, y_pos)
                        tex_node.image = image

                        # Set color space based on map type
                        if map_type.lower() in ["color", "diffuse", "albedo"]:
                            try:
                                tex_node.image.colorspace_settings.name = "sRGB"
                            except:
                                pass  # Use default if sRGB not available
                        else:
                            try:
                                tex_node.image.colorspace_settings.name = "Non-Color"
                            except:
                                pass  # Use default if Non-Color not available

                        links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

                        # Connect to appropriate input on Principled BSDF
                        if map_type.lower() in ["color", "diffuse", "albedo"]:
                            links.new(
                                tex_node.outputs["Color"],
                                principled.inputs["Base Color"],
                            )
                        elif map_type.lower() in ["roughness", "rough"]:
                            links.new(
                                tex_node.outputs["Color"],
                                principled.inputs["Roughness"],
                            )
                        elif map_type.lower() in ["metallic", "metalness", "metal"]:
                            links.new(
                                tex_node.outputs["Color"], principled.inputs["Metallic"]
                            )
                        elif map_type.lower() in ["normal", "nor"]:
                            # Add normal map node
                            normal_map = nodes.new(type="ShaderNodeNormalMap")
                            normal_map.location = (x_pos + 200, y_pos)
                            links.new(
                                tex_node.outputs["Color"], normal_map.inputs["Color"]
                            )
                            links.new(
                                normal_map.outputs["Normal"],
                                principled.inputs["Normal"],
                            )
                        elif map_type in ["displacement", "disp", "height"]:
                            # Add displacement node
                            disp_node = nodes.new(type="ShaderNodeDisplacement")
                            disp_node.location = (x_pos + 200, y_pos - 200)
                            links.new(
                                tex_node.outputs["Color"], disp_node.inputs["Height"]
                            )
                            links.new(
                                disp_node.outputs["Displacement"],
                                output.inputs["Displacement"],
                            )

                        y_pos -= 250

                    return {
                        "success": True,
                        "message": f"Texture {asset_id} imported as material",
                        "material": mat.name,
                        "maps": list(downloaded_maps.keys()),
                    }

                except Exception as e:
                    return {"error": f"Failed to process textures: {str(e)}"}

            elif asset_type == "models":
                # For models, prefer glTF format if available
                if not file_format:
                    file_format = "gltf"  # Default format for models

                if file_format in files_data and resolution in files_data[file_format]:
                    file_info = files_data[file_format][resolution][file_format]
                    file_url = file_info["url"]

                    # Create a temporary directory to store the model and its dependencies
                    temp_dir = tempfile.mkdtemp()
                    main_file_path = ""

                    try:
                        # Download the main model file
                        main_file_name = file_url.split("/")[-1]
                        main_file_path = os.path.join(temp_dir, main_file_name)

                        response = requests.get(file_url, timeout=2)
                        if response.status_code != 200:
                            return {
                                "error": f"Failed to download model: {response.status_code}"
                            }

                        with open(main_file_path, "wb") as f:
                            f.write(response.content)

                        # Check for included files and download them
                        if "include" in file_info and file_info["include"]:
                            for include_path, include_info in file_info[
                                "include"
                            ].items():
                                # Get the URL for the included file - this is the fix
                                include_url = include_info["url"]

                                # Create the directory structure for the included file
                                include_file_path = os.path.join(temp_dir, include_path)
                                os.makedirs(
                                    os.path.dirname(include_file_path), exist_ok=True
                                )

                                # Download the included file
                                include_response = requests.get(include_url, timeout=2)
                                if include_response.status_code == 200:
                                    with open(include_file_path, "wb") as f:
                                        f.write(include_response.content)
                                else:
                                    print(
                                        f"Failed to download included file: {include_path}"
                                    )

                        # Import the model into Blender
                        if file_format == "gltf" or file_format == "glb":
                            bpy.ops.import_scene.gltf(filepath=main_file_path)
                        elif file_format == "fbx":
                            bpy.ops.import_scene.fbx(filepath=main_file_path)
                        elif file_format == "obj":
                            bpy.ops.import_scene.obj(filepath=main_file_path)
                        elif file_format == "blend":
                            # For blend files, we need to append or link
                            with bpy.data.libraries.load(
                                main_file_path, link=False
                            ) as (data_from, data_to):
                                data_to.objects = data_from.objects

                            # Link the objects to the scene
                            for obj in data_to.objects:
                                if obj is not None:
                                    bpy.context.collection.objects.link(obj)
                        else:
                            return {"error": f"Unsupported model format: {file_format}"}

                        # Get the names of imported objects
                        imported_objects = [
                            obj.name for obj in bpy.context.selected_objects
                        ]

                        return {
                            "success": True,
                            "message": f"Model {asset_id} imported successfully",
                            "imported_objects": imported_objects,
                        }
                    except Exception as e:
                        return {"error": f"Failed to import model: {str(e)}"}
                    finally:
                        # Clean up temporary directory
                        try:
                            shutil.rmtree(temp_dir)
                        except:
                            print(f"Failed to clean up temporary directory: {temp_dir}")
                else:
                    return {
                        "error": f"Requested format or resolution not available for this model"
                    }

            else:
                return {"error": f"Unsupported asset type: {asset_type}"}

        except Exception as e:
            return {"error": f"Failed to download asset: {str(e)}"}

    def set_texture(self, object_name, texture_id):
        """Apply a previously downloaded Polyhaven texture to an object by creating a new material"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                return {"error": f"Object not found: {object_name}"}

            # Make sure object can accept materials
            if not hasattr(obj, "data") or not hasattr(obj.data, "materials"):
                return {"error": f"Object {object_name} cannot accept materials"}

            # Find all images related to this texture and ensure they're properly loaded
            texture_images = {}
            for img in bpy.data.images:
                if img.name.startswith(texture_id + "_"):
                    # Extract the map type from the image name
                    map_type = img.name.split("_")[-1].split(".")[0]

                    # Force a reload of the image
                    img.reload()

                    # Ensure proper color space
                    if map_type.lower() in ["color", "diffuse", "albedo"]:
                        try:
                            img.colorspace_settings.name = "sRGB"
                        except:
                            pass
                    else:
                        try:
                            img.colorspace_settings.name = "Non-Color"
                        except:
                            pass

                    # Ensure the image is packed
                    if not img.packed_file:
                        img.pack()

                    texture_images[map_type] = img
                    print(f"Loaded texture map: {map_type} - {img.name}")

                    # Debug info
                    print(f"Image size: {img.size[0]}x{img.size[1]}")
                    print(f"Color space: {img.colorspace_settings.name}")
                    print(f"File format: {img.file_format}")
                    print(f"Is packed: {bool(img.packed_file)}")

            if not texture_images:
                return {
                    "error": f"No texture images found for: {texture_id}. Please download the texture first."
                }

            # Create a new material
            new_mat_name = f"{texture_id}_material_{object_name}"

            # Remove any existing material with this name to avoid conflicts
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                bpy.data.materials.remove(existing_mat)

            new_mat = bpy.data.materials.new(name=new_mat_name)
            new_mat.use_nodes = True

            # Set up the material nodes
            nodes = new_mat.node_tree.nodes
            links = new_mat.node_tree.links

            # Clear default nodes
            nodes.clear()

            # Create output node
            output = nodes.new(type="ShaderNodeOutputMaterial")
            output.location = (600, 0)

            # Create principled BSDF node
            principled = nodes.new(type="ShaderNodeBsdfPrincipled")
            principled.location = (300, 0)
            links.new(principled.outputs[0], output.inputs[0])

            # Add texture nodes based on available maps
            tex_coord = nodes.new(type="ShaderNodeTexCoord")
            tex_coord.location = (-800, 0)

            mapping = nodes.new(type="ShaderNodeMapping")
            mapping.location = (-600, 0)
            mapping.vector_type = "TEXTURE"  # Changed from default 'POINT' to 'TEXTURE'
            links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

            # Position offset for texture nodes
            x_pos = -400
            y_pos = 300

            # Connect different texture maps
            for map_type, image in texture_images.items():
                tex_node = nodes.new(type="ShaderNodeTexImage")
                tex_node.location = (x_pos, y_pos)
                tex_node.image = image

                # Set color space based on map type
                if map_type.lower() in ["color", "diffuse", "albedo"]:
                    try:
                        tex_node.image.colorspace_settings.name = "sRGB"
                    except:
                        pass  # Use default if sRGB not available
                else:
                    try:
                        tex_node.image.colorspace_settings.name = "Non-Color"
                    except:
                        pass  # Use default if Non-Color not available

                links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

                # Connect to appropriate input on Principled BSDF
                if map_type.lower() in ["color", "diffuse", "albedo"]:
                    links.new(
                        tex_node.outputs["Color"], principled.inputs["Base Color"]
                    )
                elif map_type.lower() in ["roughness", "rough"]:
                    links.new(tex_node.outputs["Color"], principled.inputs["Roughness"])
                elif map_type.lower() in ["metallic", "metalness", "metal"]:
                    links.new(tex_node.outputs["Color"], principled.inputs["Metallic"])
                elif map_type.lower() in ["normal", "nor", "dx", "gl"]:
                    # Add normal map node
                    normal_map = nodes.new(type="ShaderNodeNormalMap")
                    normal_map.location = (x_pos + 200, y_pos)
                    links.new(tex_node.outputs["Color"], normal_map.inputs["Color"])
                    links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
                elif map_type.lower() in ["displacement", "disp", "height"]:
                    # Add displacement node
                    disp_node = nodes.new(type="ShaderNodeDisplacement")
                    disp_node.location = (x_pos + 200, y_pos - 200)
                    disp_node.inputs[
                        "Scale"
                    ].default_value = 0.1  # Reduce displacement strength
                    links.new(tex_node.outputs["Color"], disp_node.inputs["Height"])
                    links.new(
                        disp_node.outputs["Displacement"], output.inputs["Displacement"]
                    )

                y_pos -= 250

            # Second pass: Connect nodes with proper handling for special cases
            texture_nodes = {}

            # First find all texture nodes and store them by map type
            for node in nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break

            # Now connect everything using the nodes instead of images
            # Handle base color (diffuse)
            for map_name in ["color", "diffuse", "albedo"]:
                if map_name in texture_nodes:
                    links.new(
                        texture_nodes[map_name].outputs["Color"],
                        principled.inputs["Base Color"],
                    )
                    print(f"Connected {map_name} to Base Color")
                    break

            # Handle roughness
            for map_name in ["roughness", "rough"]:
                if map_name in texture_nodes:
                    links.new(
                        texture_nodes[map_name].outputs["Color"],
                        principled.inputs["Roughness"],
                    )
                    print(f"Connected {map_name} to Roughness")
                    break

            # Handle metallic
            for map_name in ["metallic", "metalness", "metal"]:
                if map_name in texture_nodes:
                    links.new(
                        texture_nodes[map_name].outputs["Color"],
                        principled.inputs["Metallic"],
                    )
                    print(f"Connected {map_name} to Metallic")
                    break

            # Handle normal maps
            for map_name in ["gl", "dx", "nor"]:
                if map_name in texture_nodes:
                    normal_map_node = nodes.new(type="ShaderNodeNormalMap")
                    normal_map_node.location = (100, 100)
                    links.new(
                        texture_nodes[map_name].outputs["Color"],
                        normal_map_node.inputs["Color"],
                    )
                    links.new(
                        normal_map_node.outputs["Normal"], principled.inputs["Normal"]
                    )
                    print(f"Connected {map_name} to Normal")
                    break

            # Handle displacement
            for map_name in ["displacement", "disp", "height"]:
                if map_name in texture_nodes:
                    disp_node = nodes.new(type="ShaderNodeDisplacement")
                    disp_node.location = (300, -200)
                    disp_node.inputs[
                        "Scale"
                    ].default_value = 0.1  # Reduce displacement strength
                    links.new(
                        texture_nodes[map_name].outputs["Color"],
                        disp_node.inputs["Height"],
                    )
                    links.new(
                        disp_node.outputs["Displacement"], output.inputs["Displacement"]
                    )
                    print(f"Connected {map_name} to Displacement")
                    break

            # Handle ARM texture (Ambient Occlusion, Roughness, Metallic)
            if "arm" in texture_nodes:
                separate_rgb = nodes.new(type="ShaderNodeSeparateRGB")
                separate_rgb.location = (-200, -100)
                links.new(
                    texture_nodes["arm"].outputs["Color"], separate_rgb.inputs["Image"]
                )

                # Connect Roughness (G) if no dedicated roughness map
                if not any(
                    map_name in texture_nodes for map_name in ["roughness", "rough"]
                ):
                    links.new(separate_rgb.outputs["G"], principled.inputs["Roughness"])
                    print("Connected ARM.G to Roughness")

                # Connect Metallic (B) if no dedicated metallic map
                if not any(
                    map_name in texture_nodes
                    for map_name in ["metallic", "metalness", "metal"]
                ):
                    links.new(separate_rgb.outputs["B"], principled.inputs["Metallic"])
                    print("Connected ARM.B to Metallic")

                # For AO (R channel), multiply with base color if we have one
                base_color_node = None
                for map_name in ["color", "diffuse", "albedo"]:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type="ShaderNodeMixRGB")
                    mix_node.location = (100, 200)
                    mix_node.blend_type = "MULTIPLY"
                    mix_node.inputs["Fac"].default_value = 0.8  # 80% influence

                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs["Color"].links:
                        if link.to_socket == principled.inputs["Base Color"]:
                            links.remove(link)

                    # Connect through the mix node
                    links.new(base_color_node.outputs["Color"], mix_node.inputs[1])
                    links.new(separate_rgb.outputs["R"], mix_node.inputs[2])
                    links.new(
                        mix_node.outputs["Color"], principled.inputs["Base Color"]
                    )
                    print("Connected ARM.R to AO mix with Base Color")

            # Handle AO (Ambient Occlusion) if separate
            if "ao" in texture_nodes:
                base_color_node = None
                for map_name in ["color", "diffuse", "albedo"]:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type="ShaderNodeMixRGB")
                    mix_node.location = (100, 200)
                    mix_node.blend_type = "MULTIPLY"
                    mix_node.inputs["Fac"].default_value = 0.8  # 80% influence

                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs["Color"].links:
                        if link.to_socket == principled.inputs["Base Color"]:
                            links.remove(link)

                    # Connect through the mix node
                    links.new(base_color_node.outputs["Color"], mix_node.inputs[1])
                    links.new(texture_nodes["ao"].outputs["Color"], mix_node.inputs[2])
                    links.new(
                        mix_node.outputs["Color"], principled.inputs["Base Color"]
                    )
                    print("Connected AO to mix with Base Color")

            # CRITICAL: Make sure to clear all existing materials from the object
            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)

            # Assign the new material to the object
            obj.data.materials.append(new_mat)

            # CRITICAL: Make the object active and select it
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)

            # CRITICAL: Force Blender to update the material
            bpy.context.view_layer.update()

            # Get the list of texture maps
            texture_maps = list(texture_images.keys())

            # Get info about texture nodes for debugging
            material_info = {
                "name": new_mat.name,
                "has_nodes": new_mat.use_nodes,
                "node_count": len(new_mat.node_tree.nodes),
                "texture_nodes": [],
            }

            for node in new_mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    connections = []
                    for output in node.outputs:
                        for link in output.links:
                            connections.append(
                                f"{output.name} → {link.to_node.name}.{link.to_socket.name}"
                            )

                    material_info["texture_nodes"].append(
                        {
                            "name": node.name,
                            "image": node.image.name,
                            "colorspace": node.image.colorspace_settings.name,
                            "connections": connections,
                        }
                    )

            return {
                "success": True,
                "message": f"Created new material and applied texture {texture_id} to {object_name}",
                "material": new_mat.name,
                "maps": texture_maps,
                "material_info": material_info,
            }

        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}

    def get_polyhaven_status(self):
        """Get the current status of PolyHaven integration"""
        enabled = bpy.context.scene.blendermcp_use_polyhaven
        if enabled:
            return {
                "enabled": True,
                "message": "PolyHaven integration is enabled and ready to use.",
            }
        else:
            return {
                "enabled": False,
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection to Claude""",
            }



# Operator to start the server
class TRELLIS_OT_StartMCPServer(bpy.types.Operator):
    bl_idname = "trellis.start_mcp_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the MCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "trellis_mcp_server") or not bpy.types.trellis_mcp_server:
            bpy.types.trellis_mcp_server = BlenderMCPServer(port=scene.trellis_mcp_port)

        # Start the server
        bpy.types.trellis_mcp_server.start()
        scene.trellis_mcp_server_running = True

        return {"FINISHED"}


# Operator to stop the server
class TRELLIS_OT_StopMCPServer(bpy.types.Operator):
    bl_idname = "trellis.stop_mcp_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "trellis_mcp_server") and bpy.types.trellis_mcp_server:
            bpy.types.trellis_mcp_server.stop()
            del bpy.types.trellis_mcp_server

        scene.trellis_mcp_server_running = False

        return {"FINISHED"}


classes = [
    TrellisProperties,
    TRELLIS_OT_convert_image,
    TRELLIS_OT_convert_text,
    TRELLIS_OT_import_result,
    TRELLIS_OT_refresh_status,
    TRELLIS_OT_show_preview,
    TRELLIS_OT_convert_mesh,
    TRELLIS_OT_convert_text_mesh,
    TRELLIS_OT_check_server,
    TRELLIS_PT_main_panel,
    TRELLIS_OT_StartMCPServer,
    TRELLIS_OT_StopMCPServer,
]


def register():
    # Register MCP server properties
    bpy.types.Scene.trellis_mcp_port = IntProperty(
        name="Port",
        description="Port for the MCP server",
        default=9876,
        min=1024,
        max=65535,
    )
    
    bpy.types.Scene.trellis_mcp_server_running = bpy.props.BoolProperty(
        name="Server Running", default=False
    )

    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False,
    )

    bpy.types.Scene.MCP_use_trellis = bpy.props.BoolProperty(
        name="MCP_use_trellis", default=True
    )
    
    # Register classes
    for cls in classes:
        register_class(cls)
    bpy.types.Scene.trellis_props = bpy.props.PointerProperty(type=TrellisProperties)
    start_auto_refresh()
    
    # Check server status on startup
    def check_server_on_startup():
        try:
            if hasattr(bpy.context.scene, 'trellis_props'):
                bpy.ops.trellis.check_server()
        except Exception:
            pass
        return None
    
    # Schedule server check after a short delay to ensure the UI is ready
    bpy.app.timers.register(check_server_on_startup, first_interval=1.0)

def unregister():
    # Stop MCP server if running
    if hasattr(bpy.types, "trellis_mcp_server") and bpy.types.trellis_mcp_server:
        bpy.types.trellis_mcp_server.stop()
        del bpy.types.trellis_mcp_server
    
    stop_auto_refresh()
    del bpy.types.Scene.trellis_props
    del bpy.types.Scene.trellis_mcp_port
    del bpy.types.Scene.trellis_mcp_server_running
    del bpy.types.Scene.blendermcp_use_polyhaven
    
    for cls in reversed(classes):
        unregister_class(cls)


if __name__ == "__main__":
    register()
