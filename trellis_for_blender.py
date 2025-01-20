bl_info = {
    "name": "TRELLIS 3D Generation",
    "author": "FishWoWater",
    "version": (0, 1),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > TRELLIS",
    "description": "3D Mesh Generation with TRELLIS",
    "category": "3D View",
}

import bpy
import os
import requests
import tempfile
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
        response = requests.get(file_url)
        response.raise_for_status()
        with open(cache_path, 'wb') as f:
            f.write(response.content)
    return cache_path


class TrellisProperties(PropertyGroup):
    api_url: StringProperty(name="Endpoint Url",
                            description="TRELLIS API URL",
                            default="http://localhost:5000",
                            maxlen=1024)
    image_path: StringProperty(name="input image path",
                               description="Path to the image file",
                               default="",
                               subtype='FILE_PATH')
    sparse_structure_sample_steps: IntProperty(name="sparse_structure sample steps",
                                               description="Number of sampling steps for sparse structure",
                                               default=12,
                                               min=1)
    sparse_structure_cfg_strength: FloatProperty(name="sparse_structure cfg strength",
                                                 description="CFG strength for sparse structure",
                                                 default=7.5,
                                                 min=0.0)
    slat_sample_steps: IntProperty(name="slat sample steps",
                                   description="Number of sampling steps for SLAT",
                                   default=12,
                                   min=1)
    slat_cfg_strength: FloatProperty(name="slat cfg strength",
                                     description="CFG strength for SLAT",
                                     default=3.5,
                                     min=0.0)
    simplify_ratio: FloatProperty(name="simplify ratio",
                                  description="Ratio of triangles to remove in simplification",
                                  default=0.95,
                                  min=0.0,
                                  max=1.0)
    texture_size: IntProperty(name="texture size", description="Size of the texture used for GLB", default=1024, min=64)
    texture_bake_mode: EnumProperty(name="tex bake mode",
                                    description="Mode for texture baking",
                                    items=[('opt', "optimized", "Optimized texture baking"),
                                           ('fast', "fast", "Fast texture baking")],
                                    default='fast')
    auto_refresh: BoolProperty(name="Auto Refresh", description="Automatically refresh request status", default=True)
    task_id: StringProperty(name="Task ID", description="Current task ID for tracking conversion progress", default="")
    show_parameters: BoolProperty(name="Show Parameters", description="Show/hide generation parameters", default=True)


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
                files = {'image': f}
                data = {
                    'sparse_structure_sample_steps': props.sparse_structure_sample_steps,
                    'sparse_structure_cfg_strength': props.sparse_structure_cfg_strength,
                    'slat_sample_steps': props.slat_sample_steps,
                    'slat_cfg_strength': props.slat_cfg_strength,
                    'simplify_ratio': props.simplify_ratio,
                    'texture_size': props.texture_size,
                    'texture_bake_mode': props.texture_bake_mode,
                    'image_name': os.path.splitext(os.path.basename(props.image_path))[0]
                }
                response = requests.post(f"{props.api_url}/image_to_3d", files=files, data=data)
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
            response = requests.get(f"{context.scene.trellis_props.api_url}/my_requests")
            response.raise_for_status()
            context.scene['trellis_requests'] = response.json()
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error refreshing status: {str(e)}")
            return {'CANCELLED'}


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

        # Show image in image editor
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = img
                    break
            else:
                # If no image editor is found, create one by splitting the 3D view
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        override = context.copy()
                        override['area'] = area
                        bpy.ops.screen.area_split(override, direction='VERTICAL', factor=0.3)
                        area.type = 'IMAGE_EDITOR'
                        area.spaces.active.image = img
                        break

        return {'FINISHED'}


class TRELLIS_OT_convert_mesh(Operator):
    bl_idname = "trellis.convert_mesh"
    bl_label = "Convert Selected to GLB"
    bl_description = "Convert selected object to GLB and process with TRELLIS"

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

            # Upload both GLB and image files to API
            with open(temp_glb, 'rb') as glb_file, open(props.image_path, 'rb') as img_file:
                files = {'mesh': glb_file, 'image': img_file}
                data = {
                    'sparse_structure_sample_steps': props.sparse_structure_sample_steps,
                    'sparse_structure_cfg_strength': props.sparse_structure_cfg_strength,
                    'slat_sample_steps': props.slat_sample_steps,
                    'slat_cfg_strength': props.slat_cfg_strength,
                    'simplify_ratio': props.simplify_ratio,
                    'texture_size': props.texture_size,
                    'texture_bake_mode': props.texture_bake_mode,
                    'image_name': os.path.splitext(os.path.basename(props.image_path))[0]
                }
                response = requests.post(f"{props.api_url}/image_to_3d", files=files, data=data)
                response.raise_for_status()
                result = response.json()

                if result['status'] == 'queued':
                    # props.task_id = result.get('request_id', '')
                    # Refresh status immediately to show the new request
                    bpy.ops.trellis.refresh_status()
                    self.report({'INFO'}, f"Request queued with ID: {props.task_id}")
                    return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}
        finally:
            # Clean up temporary files
            os.remove(temp_glb)
            os.rmdir(temp_dir)

        return {'FINISHED'}


class TRELLIS_PT_main_panel(Panel):
    bl_label = "TRELLIS Image to 3D"
    bl_idname = "TRELLIS_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'TRELLIS'

    def draw(self, context):
        layout = self.layout
        props = context.scene.trellis_props

        # API Configuration
        box = layout.box()
        box.label(text="API Configuration")
        box.prop(props, "api_url")

        # Image selection
        box = layout.box()
        row = box.row()
        row.prop(props, "image_path")

        # Preview button
        if props.image_path and os.path.exists(props.image_path):
            row = box.row()
            row.operator("trellis.show_preview", text="Preview Image", icon='IMAGE_DATA')

        # Parameters with collapse button
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

        # Convert buttons
        layout.operator("trellis.convert_image", text="Image to 3D", icon='MESH_CUBE')
        layout.operator("trellis.convert_mesh", text="Image-Conditioned Detail Variation", icon='MESH_CUBE')

        # History section in a single box
        history_box = layout.box()
        row = history_box.row()
        row.label(text="History:")
        row = history_box.row()
        row.prop(props, "auto_refresh")
        row.operator("trellis.refresh_status", text="", icon='FILE_REFRESH')

        if 'trellis_requests' in context.scene:
            requests = context.scene['trellis_requests'].get('requests', [])
            for req in requests:
                row = history_box.row(align=True)
                # Show image name + first 8 chars of UUID
                display_name = f"{req.get('image_name', '')}(ID-{req['request_id'][:8]}-...)"
                row.label(text=display_name)
                row.label(text=req['status'])

                if req['status'] == 'complete' and req.get('output_files'):
                    for file_url in req['output_files']:
                        if file_url.endswith('.glb'):
                            op = row.operator("trellis.import_result", text="", icon='IMPORT')
                            op.file_url = file_url


def auto_refresh_callback():
    try:
        if bpy.context.scene.trellis_props.auto_refresh:
            bpy.ops.trellis.refresh_status()
            return 3.0  # Return time until next execution
        return None  # Stop timer if auto_refresh is disabled
    except ReferenceError:
        return None  # Stop timer if context is invalid


def start_auto_refresh():
    if not bpy.app.timers.is_registered(auto_refresh_callback):
        bpy.app.timers.register(auto_refresh_callback, persistent=True)


classes = [
    TrellisProperties,
    TRELLIS_OT_convert_image,
    TRELLIS_OT_import_result,
    TRELLIS_OT_refresh_status,
    TRELLIS_OT_show_preview,
    TRELLIS_OT_convert_mesh,
    TRELLIS_PT_main_panel,
]


def register():
    for cls in classes:
        register_class(cls)
    bpy.types.Scene.trellis_props = bpy.props.PointerProperty(type=TrellisProperties)

    # Start auto-refresh thread
    start_auto_refresh()


def unregister():
    del bpy.types.Scene.trellis_props
    for cls in reversed(classes):
        unregister_class(cls)


if __name__ == "__main__":
    register()
