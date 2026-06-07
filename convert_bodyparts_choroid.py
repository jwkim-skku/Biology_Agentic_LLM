from pathlib import Path

import trimesh
from trimesh.visual.material import PBRMaterial


BASE = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\bodyparts3d_choroid_plexus"
)

PARTS = [
    ("FMA274027", "BodyParts3D_Right_Choroid_Plexus", [1.0, 0.42, 0.78, 1.0]),
    ("FMA274029", "BodyParts3D_Left_Choroid_Plexus", [0.72, 0.46, 1.0, 1.0]),
]


def material(name, color):
    return PBRMaterial(
        name=f"{name}_material",
        baseColorFactor=color,
        roughnessFactor=0.45,
        metallicFactor=0.0,
        doubleSided=True,
    )


def main():
    scene = trimesh.Scene()

    for fma_id, name, color in PARTS:
        mesh = trimesh.load(BASE / f"{fma_id}.stl", force="mesh")
        mesh.metadata["name"] = name
        mesh.visual.material = material(name, color)
        scene.add_geometry(mesh, node_name=name)

        single = trimesh.Scene()
        single.add_geometry(mesh.copy(), node_name=name)
        (BASE / f"{fma_id}.glb").write_bytes(single.export(file_type="glb"))

    bounds = scene.bounds
    center = bounds.mean(axis=0)
    for geometry in scene.geometry.values():
        geometry.apply_translation(-center)

    (BASE / "bodyparts3d_choroid_plexus_left_right.glb").write_bytes(
        scene.export(file_type="glb")
    )

    print(BASE / "bodyparts3d_choroid_plexus_left_right.glb")


if __name__ == "__main__":
    main()
