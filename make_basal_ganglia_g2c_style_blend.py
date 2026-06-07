from pathlib import Path

import bpy
from mathutils import Vector


SOURCE_GLB = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\freesurfer_cortex\freesurfer_full_aparc_aseg_atlas.glb"
)
OUT_BLEND = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\Blender_PDSE\BasalGanglia_G2C_style_work.blend"
)
OUT_GLB = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\freesurfer_cortex\basal_ganglia_g2c_style.glb"
)


def make_material(name, color, alpha=1.0, roughness=0.42):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.blend_method = "BLEND" if alpha < 1 else "OPAQUE"
    material.use_screen_refraction = alpha < 1
    material.show_transparent_back = True

    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Alpha"].default_value = alpha
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Specular"].default_value = 0.42
    material.diffuse_color = color
    return material


def ensure_collection(name):
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(obj, collection):
    for source in list(obj.users_collection):
        source.objects.unlink(obj)
    collection.objects.link(obj)


def is_basal_ganglia(name):
    lower = name.lower()
    return "caudate" in lower or "putamen" in lower or "pallidum" in lower


def bg_material_for(name, mats):
    lower = name.lower()
    if "caudate" in lower:
        return mats["caudate"]
    if "putamen" in lower:
        return mats["putamen"]
    return mats["pallidum"]


def add_polish_modifiers(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.shade_smooth()
    obj.data.use_auto_smooth = True

    smooth = obj.modifiers.new("G2C-style surface smoothing", "SMOOTH")
    smooth.factor = 0.34
    smooth.iterations = 12

    weighted = obj.modifiers.new("Clean educational normals", "WEIGHTED_NORMAL")
    weighted.keep_sharp = True
    weighted.weight = 70

    obj.select_set(False)


def scene_bounds(objects):
    points = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            points.append(obj.matrix_world @ Vector(corner))
    if not points:
        return Vector((0, 0, 0)), 1
    min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (min_v + max_v) / 2
    radius = max((p - center).length for p in points)
    return center, radius


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.import_scene.gltf(filepath=str(SOURCE_GLB))

    reference_collection = ensure_collection("01_transparent_brain_reference")
    polished_collection = ensure_collection("02_basal_ganglia_polished")
    hidden_collection = ensure_collection("03_hidden_context_structures")

    mats = {
        "brain_shell": make_material("transparent_white_brain_shell", (0.88, 0.94, 0.94, 0.11), 0.11, 0.36),
        "caudate": make_material("caudate_g2c_purple", (0.62, 0.17, 0.92, 1.0), 1.0, 0.34),
        "putamen": make_material("putamen_g2c_blue", (0.23, 0.42, 0.95, 1.0), 1.0, 0.36),
        "pallidum": make_material("pallidum_g2c_pink", (1.0, 0.43, 0.70, 1.0), 1.0, 0.35),
        "hidden": make_material("hidden_context_gray", (0.55, 0.58, 0.56, 0.08), 0.08, 0.6),
    }

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]

    for obj in mesh_objects:
        lower = obj.name.lower()
        obj.data.materials.clear()

        if lower.startswith("lh_") or lower.startswith("rh_"):
            obj.data.materials.append(mats["brain_shell"])
            obj.show_transparent = True
            obj.hide_render = False
            obj.display_type = "TEXTURED"
            move_to_collection(obj, reference_collection)
        elif is_basal_ganglia(obj.name):
            obj.data.materials.append(bg_material_for(obj.name, mats))
            obj.hide_render = False
            obj.display_type = "TEXTURED"
            move_to_collection(obj, polished_collection)
            add_polish_modifiers(obj)
        else:
            obj.data.materials.append(mats["hidden"])
            obj.hide_viewport = True
            obj.hide_render = True
            move_to_collection(obj, hidden_collection)

    center, radius = scene_bounds([obj for obj in bpy.context.scene.objects if obj.type == "MESH" and not obj.hide_render])
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            obj.location -= center

    bpy.ops.object.light_add(type="AREA", location=(0, -180, 130))
    key = bpy.context.object
    key.name = "large_soft_key_light"
    key.data.energy = 520
    key.data.size = 160

    bpy.ops.object.light_add(type="POINT", location=(-100, 80, 90))
    fill = bpy.context.object
    fill.name = "soft_fill_light"
    fill.data.energy = 95

    bpy.ops.object.camera_add(location=(0, -radius * 2.35, radius * 0.42), rotation=(1.34, 0, 0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    camera.name = "g2c_style_preview_camera"
    camera.data.lens = 70

    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.context.scene.eevee.use_gtao = True
    bpy.context.scene.eevee.gtao_distance = 3
    bpy.context.scene.eevee.gtao_factor = 1.6
    bpy.context.scene.view_settings.view_transform = "Filmic"
    bpy.context.scene.view_settings.look = "Medium High Contrast"
    bpy.context.scene.view_settings.exposure = 0
    bpy.context.scene.view_settings.gamma = 1

    OUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
    OUT_GLB.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND))
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and not obj.hide_render:
            obj.hide_select = False
            obj.select_set(True)

    bpy.ops.export_scene.gltf(
        filepath=str(OUT_GLB),
        export_format="GLB",
        export_apply=True,
        use_selection=True,
    )

    print(f"Saved blend: {OUT_BLEND}")
    print(f"Saved glb: {OUT_GLB}")


if __name__ == "__main__":
    main()
