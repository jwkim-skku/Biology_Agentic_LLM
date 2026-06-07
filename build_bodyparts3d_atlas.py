from pathlib import Path

import trimesh
from trimesh.visual.material import PBRMaterial


BASE = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\bodyparts3d_atlas"
)

PARTS = [
    ("FMA67944", "Cerebellum", [0.55, 0.47, 0.42, 0.92]),
    ("FMA67943", "Pons", [0.62, 0.54, 0.48, 0.92]),
    ("FMA62004", "Medulla_Oblongata", [0.58, 0.51, 0.46, 0.92]),
    ("FMA86464", "Corpus_Callosum", [0.93, 0.84, 0.70, 0.96]),
    ("FMA258714", "Right_Thalamus", [0.62, 0.55, 0.85, 0.96]),
    ("FMA258716", "Left_Thalamus", [0.62, 0.55, 0.85, 0.96]),
    ("FMA72713", "Right_Hippocampus", [0.78, 0.55, 0.92, 0.98]),
    ("FMA72714", "Left_Hippocampus", [0.78, 0.55, 0.92, 0.98]),
    ("FMA72832", "Right_Amygdala", [0.95, 0.42, 0.52, 1.0]),
    ("FMA72833", "Left_Amygdala", [0.95, 0.42, 0.52, 1.0]),
    ("FMA72826", "Right_Caudate_Nucleus", [0.75, 0.50, 0.90, 0.98]),
    ("FMA72827", "Left_Caudate_Nucleus", [0.75, 0.50, 0.90, 0.98]),
    ("FMA72828", "Right_Putamen", [0.45, 0.58, 0.92, 0.96]),
    ("FMA72829", "Left_Putamen", [0.45, 0.58, 0.92, 0.96]),
    ("FMA78449", "Right_Lateral_Ventricle", [0.72, 0.88, 0.94, 0.45]),
    ("FMA78450", "Left_Lateral_Ventricle", [0.72, 0.88, 0.94, 0.45]),
    ("FMA78454", "Third_Ventricle", [0.72, 0.88, 0.94, 0.45]),
    ("FMA274027", "Right_Choroid_Plexus", [1.00, 0.36, 0.78, 1.0]),
    ("FMA274029", "Left_Choroid_Plexus", [0.70, 0.46, 1.00, 1.0]),
]


def make_material(name, color):
    return PBRMaterial(
        name=f"{name}_material",
        baseColorFactor=color,
        roughnessFactor=0.42,
        metallicFactor=0.0,
        doubleSided=True,
        alphaMode="BLEND" if color[3] < 0.99 else "OPAQUE",
    )


def main():
    scene = trimesh.Scene()

    for fma_id, name, color in PARTS:
        mesh = trimesh.load(BASE / f"{fma_id}.stl", force="mesh")
        mesh.metadata["name"] = name
        mesh.visual.material = make_material(name, color)
        scene.add_geometry(mesh, node_name=name)

    bounds = scene.bounds
    center = bounds.mean(axis=0)
    for geometry in scene.geometry.values():
        geometry.apply_translation(-center)

    out = BASE / "bodyparts3d_brain_atlas.glb"
    out.write_bytes(scene.export(file_type="glb"))
    print(out)


if __name__ == "__main__":
    main()
