from pathlib import Path
from math import radians

import bpy
from mathutils import Vector


SOURCE_BLEND = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\Blender_PDSE\BasalGanglia_G2C_style_work.blend"
)
OUT_BLEND = SOURCE_BLEND
OUT_GLB = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\freesurfer_cortex\basal_ganglia_illustrated.glb"
)


def make_material(name, color, alpha=1.0, roughness=0.38, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.blend_method = "BLEND" if alpha < 1 else "OPAQUE"
    mat.use_screen_refraction = alpha < 1
    mat.show_transparent_back = True
    mat.diffuse_color = color

    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Alpha"].default_value = alpha
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Specular"].default_value = 0.48
    return mat


def collection(name):
    existing = bpy.data.collections.get(name)
    if existing:
        for obj in list(existing.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        return existing

    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def link_to(obj, col):
    for source in list(obj.users_collection):
        source.objects.unlink(obj)
    col.objects.link(obj)


def add_ellipsoid(name, loc, scale, mat, col, rotation=(0, 0, 0), segments=64, rings=32):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments,
        ring_count=rings,
        location=loc,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()

    subdiv = obj.modifiers.new("soft educational surface", "SUBSURF")
    subdiv.levels = 1
    subdiv.render_levels = 1
    weighted = obj.modifiers.new("clean normals", "WEIGHTED_NORMAL")
    weighted.keep_sharp = True
    link_to(obj, col)
    return obj


def add_curve_tube(name, points, mat, col, bevel=1.4, resolution=24):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = resolution
    curve.bevel_depth = bevel
    curve.bevel_resolution = 8
    curve.fill_mode = "FULL"

    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for point, co in zip(spline.bezier_points, points):
        point.co = Vector(co)
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"

    obj = bpy.data.objects.new(name, curve)
    obj.data.materials.append(mat)
    col.objects.link(obj)
    return obj


def add_flat_plate(name, loc, scale, mat, col, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=64,
        ring_count=16,
        location=loc,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    link_to(obj, col)
    return obj


def hide_reference_structures():
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        lower = obj.name.lower()
        if any(key in lower for key in ["caudate", "putamen", "pallidum"]):
            if not lower.startswith("illustrated_"):
                obj.hide_viewport = True
                obj.hide_render = True


def main():
    bpy.ops.wm.open_mainfile(filepath=str(SOURCE_BLEND))
    illustrated = collection("04_illustrated_g2c_style_basal_ganglia")

    mats = {
        "caudate": make_material("illustrated_caudate_deep_purple", (0.45, 0.08, 0.72, 1.0)),
        "putamen": make_material("illustrated_putamen_rosy_lilac", (0.78, 0.53, 0.72, 1.0)),
        "pallidum": make_material("illustrated_pallidum_blue_violet", (0.34, 0.42, 0.86, 1.0)),
        "thalamus": make_material("illustrated_translucent_thalamus_plate", (0.76, 0.78, 0.70, 0.42), 0.42, 0.5),
        "connector": make_material("illustrated_connectors_gray", (0.48, 0.43, 0.38, 0.72), 0.72, 0.5),
        "accent": make_material("illustrated_dark_accent_dots", (0.06, 0.04, 0.08, 1.0), 1.0, 0.32),
    }

    hide_reference_structures()

    # Left cluster
    add_ellipsoid(
        "illustrated_Left_Putamen",
        (-23, 8, -1),
        (11.5, 5.8, 7.2),
        mats["putamen"],
        illustrated,
        rotation=(radians(3), radians(-18), radians(8)),
    )
    add_ellipsoid(
        "illustrated_Left_Pallidum",
        (-20.5, 3.5, -4.3),
        (7.0, 3.0, 3.5),
        mats["pallidum"],
        illustrated,
        rotation=(radians(0), radians(-22), radians(10)),
    )
    add_curve_tube(
        "illustrated_Left_Caudate_Ribbon",
        [(-32, 22, 9), (-27, 28, 17), (-17, 27, 19), (-9, 17, 13), (-10, 5, 4)],
        mats["caudate"],
        illustrated,
        bevel=1.35,
    )

    # Right cluster
    add_ellipsoid(
        "illustrated_Right_Putamen",
        (23, 8, -1),
        (11.5, 5.8, 7.2),
        mats["putamen"],
        illustrated,
        rotation=(radians(3), radians(18), radians(-8)),
    )
    add_ellipsoid(
        "illustrated_Right_Pallidum",
        (20.5, 3.5, -4.3),
        (7.0, 3.0, 3.5),
        mats["pallidum"],
        illustrated,
        rotation=(radians(0), radians(22), radians(-10)),
    )
    add_curve_tube(
        "illustrated_Right_Caudate_Ribbon",
        [(32, 22, 9), (27, 28, 17), (17, 27, 19), (9, 17, 13), (10, 5, 4)],
        mats["caudate"],
        illustrated,
        bevel=1.35,
    )

    # Broad translucent central plates mimic the educational context shapes in G2C.
    add_flat_plate(
        "illustrated_left_thalamic_context_plate",
        (-15, 1, -8),
        (16, 2.1, 8),
        mats["thalamus"],
        illustrated,
        rotation=(radians(0), radians(-18), radians(7)),
    )
    add_flat_plate(
        "illustrated_right_thalamic_context_plate",
        (15, 1, -8),
        (16, 2.1, 8),
        mats["thalamus"],
        illustrated,
        rotation=(radians(0), radians(18), radians(-7)),
    )

    add_curve_tube(
        "illustrated_left_lower_connector",
        [(-26, -4, -9), (-21, -8, -12), (-13, -8, -12), (-5, -4, -9)],
        mats["connector"],
        illustrated,
        bevel=0.85,
    )
    add_curve_tube(
        "illustrated_right_lower_connector",
        [(26, -4, -9), (21, -8, -12), (13, -8, -12), (5, -4, -9)],
        mats["connector"],
        illustrated,
        bevel=0.85,
    )

    # Small dark accent beads along one caudate arc, echoing the visual language
    # without copying the G2C model.
    for index, x in enumerate([-25, -22, -19, -16, -13]):
        add_ellipsoid(
            f"illustrated_left_caudate_accent_{index + 1}",
            (x, 27.4, 18.5 - abs(index - 2) * 0.6),
            (0.9, 0.55, 0.55),
            mats["accent"],
            illustrated,
            rotation=(0, 0, radians(12)),
            segments=24,
            rings=12,
        )

    for index, x in enumerate([25, 22, 19, 16, 13]):
        add_ellipsoid(
            f"illustrated_right_caudate_accent_{index + 1}",
            (x, 27.4, 18.5 - abs(index - 2) * 0.6),
            (0.9, 0.55, 0.55),
            mats["accent"],
            illustrated,
            rotation=(0, 0, radians(-12)),
            segments=24,
            rings=12,
        )

    # Select only the transparent cortex reference and illustrated objects for export.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.scene.objects:
        lower = obj.name.lower()
        if obj.name.startswith("illustrated_") or lower.startswith("lh_") or lower.startswith("rh_"):
            obj.hide_viewport = False
            obj.hide_render = False
            obj.select_set(True)

    bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND))
    OUT_GLB.parent.mkdir(parents=True, exist_ok=True)
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
