from pathlib import Path

import nibabel as nib
import numpy as np
import trimesh
from nibabel.freesurfer.io import read_annot, read_geometry
from scipy import ndimage as ndi
from skimage.measure import marching_cubes
from trimesh.visual.material import PBRMaterial


SUBJECT = Path(r"E:\freesurfer_work\subjects\mybrain")
SOURCE_CHOROID_ASEG = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\brain_data\aseg.mgz"
)
OUT_DIR = Path(
    r"E:\SKKU\SKKU_26-1학기\내부인턴십_PDSE\react\Brain\public\models\freesurfer_cortex"
)

PARCEL_COLORS = [
    [0.92, 0.82, 0.77, 0.92],
    [0.88, 0.72, 0.68, 0.92],
    [0.94, 0.86, 0.78, 0.92],
    [0.84, 0.68, 0.65, 0.92],
    [0.92, 0.77, 0.66, 0.92],
    [0.82, 0.68, 0.60, 0.92],
    [0.92, 0.64, 0.80, 0.94],
    [0.82, 0.52, 0.78, 0.94],
    [0.86, 0.62, 0.86, 0.94],
    [0.78, 0.56, 0.88, 0.94],
]

SUBCORTICAL = [
    ("Brain_Stem", [16], [0.58, 0.50, 0.45, 0.96], 0.9),
    ("Left_Cerebellum_Cortex", [8], [0.56, 0.48, 0.42, 0.92], 0.8),
    ("Right_Cerebellum_Cortex", [47], [0.56, 0.48, 0.42, 0.92], 0.8),
    ("Left_Thalamus", [10], [0.62, 0.55, 0.86, 0.96], 0.65),
    ("Right_Thalamus", [49], [0.62, 0.55, 0.86, 0.96], 0.65),
    ("Left_Caudate", [11], [0.76, 0.50, 0.90, 0.98], 0.65),
    ("Right_Caudate", [50], [0.76, 0.50, 0.90, 0.98], 0.65),
    ("Left_Putamen", [12], [0.46, 0.58, 0.92, 0.96], 0.65),
    ("Right_Putamen", [51], [0.46, 0.58, 0.92, 0.96], 0.65),
    ("Left_Pallidum", [13], [0.50, 0.70, 0.92, 0.96], 0.55),
    ("Right_Pallidum", [52], [0.50, 0.70, 0.92, 0.96], 0.55),
    ("Left_Hippocampus", [17], [0.78, 0.55, 0.92, 0.98], 0.55),
    ("Right_Hippocampus", [53], [0.78, 0.55, 0.92, 0.98], 0.55),
    ("Left_Amygdala", [18], [0.95, 0.42, 0.52, 1.0], 0.45),
    ("Right_Amygdala", [54], [0.95, 0.42, 0.52, 1.0], 0.45),
    ("Left_Lateral_Ventricle", [4], [0.72, 0.88, 0.94, 0.38], 0.45),
    ("Right_Lateral_Ventricle", [43], [0.72, 0.88, 0.94, 0.38], 0.45),
    ("Third_Ventricle", [14], [0.72, 0.88, 0.94, 0.38], 0.35),
    ("Fourth_Ventricle", [15], [0.72, 0.88, 0.94, 0.38], 0.35),
]

CHOROID = [
    ("Left_Choroid_Plexus", [31], [0.70, 0.46, 1.00, 1.0], 0.25),
    ("Right_Choroid_Plexus", [63], [1.00, 0.36, 0.78, 1.0], 0.25),
]


def color_for_name(name):
    value = 0
    for char in name:
        value = (value * 31 + ord(char)) & 0xFFFFFFFF
    return PARCEL_COLORS[value % len(PARCEL_COLORS)]


def make_material(name, color):
    return PBRMaterial(
        name=f"{name}_material",
        baseColorFactor=color,
        roughnessFactor=0.48,
        metallicFactor=0.0,
        doubleSided=True,
        alphaMode="BLEND" if color[3] < 0.99 else "OPAQUE",
    )


def add_cortex(scene, hemi):
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

        full_name = f"{hemi}_{name}"
        if hemi == "lh":
            color = [0.68, 0.72, 0.70, 0.13]
        else:
            color = color_for_name(name)

        mesh.metadata["name"] = full_name
        mesh.visual.material = make_material(full_name, color)
        scene.add_geometry(mesh, node_name=full_name)


def mask_to_mesh(mask, affine, name, color, sigma):
    if not np.any(mask):
        return None

    volume = np.pad(mask.astype(np.float32), 1, mode="constant")
    if sigma:
        volume = ndi.gaussian_filter(volume, sigma=sigma)

    verts, faces, _, _ = marching_cubes(
        volume,
        level=0.5,
        step_size=1,
        allow_degenerate=False,
    )
    verts -= 1
    world = nib.affines.apply_affine(affine, verts)
    mesh = trimesh.Trimesh(world, faces, process=True)
    mesh.metadata["name"] = name
    mesh.visual.material = make_material(name, color)
    return mesh


def add_subcortical(scene):
    img = nib.load(SUBJECT / "mri" / "aseg.mgz")
    data = np.asanyarray(img.dataobj).astype(np.int16)
    affine = img.affine

    for name, labels, color, sigma in SUBCORTICAL:
        mesh = mask_to_mesh(np.isin(data, labels), affine, name, color, sigma)
        if mesh is not None:
            scene.add_geometry(mesh, node_name=name)

    choroid_img = nib.load(SOURCE_CHOROID_ASEG)
    choroid_data = np.asanyarray(choroid_img.dataobj).astype(np.int16)
    for name, labels, color, sigma in CHOROID:
        mesh = mask_to_mesh(np.isin(choroid_data, labels), choroid_img.affine, name, color, sigma)
        if mesh is not None:
            scene.add_geometry(mesh, node_name=name)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene = trimesh.Scene()
    add_cortex(scene, "lh")
    add_cortex(scene, "rh")
    add_subcortical(scene)

    bounds = scene.bounds
    center = bounds.mean(axis=0)
    for geometry in scene.geometry.values():
        geometry.apply_translation(-center)

    out = OUT_DIR / "freesurfer_full_aparc_aseg_atlas.glb"
    out.write_bytes(scene.export(file_type="glb"))
    print(out)


if __name__ == "__main__":
    main()
