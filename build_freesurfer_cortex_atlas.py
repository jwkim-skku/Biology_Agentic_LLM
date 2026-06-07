from pathlib import Path

import numpy as np
import trimesh
from nibabel.freesurfer.io import read_annot, read_geometry
from trimesh.visual.material import PBRMaterial


SUBJECT = Path(r"E:\freesurfer_work\subjects\mybrain")
OUT_DIR = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\freesurfer_cortex"
)

LOBE_COLORS = {
    "frontal": [0.90, 0.80, 0.74, 0.90],
    "parietal": [0.86, 0.76, 0.70, 0.90],
    "temporal": [0.82, 0.70, 0.64, 0.90],
    "occipital": [0.88, 0.78, 0.70, 0.90],
    "cingulate": [0.86, 0.42, 0.72, 0.96],
    "limbic": [0.76, 0.58, 0.86, 0.94],
    "other": [0.88, 0.80, 0.74, 0.88],
}


def label_to_lobe(name):
    if "cingulate" in name:
        return "cingulate"
    if any(k in name for k in ["frontal", "precentral", "paracentral", "pars"]):
        return "frontal"
    if any(k in name for k in ["parietal", "postcentral", "precuneus", "supramarginal"]):
        return "parietal"
    if any(k in name for k in ["temporal", "fusiform", "entorhinal", "parahippocampal", "bankssts"]):
        return "temporal"
    if any(k in name for k in ["occipital", "cuneus", "pericalcarine", "lingual"]):
        return "occipital"
    if any(k in name for k in ["insula", "hippocampal", "orbitofrontal"]):
        return "limbic"
    return "other"


def make_material(name, color, transparent=False):
    rgba = color.copy()
    alpha_mode = "BLEND" if rgba[3] < 0.99 or transparent else "OPAQUE"
    if transparent:
        rgba = [0.72, 0.76, 0.74, 0.18]

    return PBRMaterial(
        name=f"{name}_material",
        baseColorFactor=rgba,
        roughnessFactor=0.62,
        metallicFactor=0.0,
        doubleSided=True,
        alphaMode=alpha_mode,
    )


def add_hemi(scene, hemi):
    vertices, faces = read_geometry(SUBJECT / "surf" / f"{hemi}.pial.T1")
    labels, _, names = read_annot(SUBJECT / "label" / f"{hemi}.aparc.annot")

    for label_index, raw_name in enumerate(names):
        name = raw_name.decode("utf-8") if hasattr(raw_name, "decode") else str(raw_name)
        if name in {"unknown", "corpuscallosum"}:
            continue

        face_mask = np.all(labels[faces] == label_index, axis=1)
        if not np.any(face_mask):
            continue

        parcel_faces = faces[face_mask]
        used_vertices, inverse = np.unique(parcel_faces.reshape(-1), return_inverse=True)
        parcel_vertices = vertices[used_vertices]
        parcel_faces = inverse.reshape((-1, 3))

        mesh = trimesh.Trimesh(parcel_vertices, parcel_faces, process=False)
        mesh.metadata["name"] = f"{hemi}_{name}"

        transparent = hemi == "lh"
        color = LOBE_COLORS[label_to_lobe(name)]
        mesh.visual.material = make_material(f"{hemi}_{name}", color, transparent)
        scene.add_geometry(mesh, node_name=f"{hemi}_{name}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene = trimesh.Scene()
    add_hemi(scene, "lh")
    add_hemi(scene, "rh")

    bounds = scene.bounds
    center = bounds.mean(axis=0)
    for geometry in scene.geometry.values():
        geometry.apply_translation(-center)

    out = OUT_DIR / "freesurfer_cortex_aparc.glb"
    out.write_bytes(scene.export(file_type="glb"))
    print(out)


if __name__ == "__main__":
    main()
