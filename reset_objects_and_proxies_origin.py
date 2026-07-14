import math

import bpy
from mathutils import Matrix


SOURCE_COLLECTION_NAME = "Objects"
PROXY_COLLECTION_NAME = "Object_Proxy"
PROXY_X_ROTATION_DEGREES = 90.0


def mesh_objects_in_collection(collection_name):
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise RuntimeError(f'Could not find collection named "{collection_name}"')

    return sorted(
        (obj for obj in collection.all_objects if obj.type == "MESH"),
        key=lambda obj: obj.name.lower(),
    )


def make_mesh_single_user(obj):
    if obj.data.users > 1:
        obj.data = obj.data.copy()


def matrix_without_translation(matrix):
    result = matrix.copy()
    result.translation = (0.0, 0.0, 0.0)
    return result


def apply_object_rotation_and_scale(obj):
    make_mesh_single_user(obj)
    transform = matrix_without_translation(obj.matrix_basis)
    if transform != Matrix.Identity(4):
        obj.data.transform(transform)
        obj.data.update()

    obj.matrix_basis = Matrix.Identity(4)
    obj.location = (0.0, 0.0, 0.0)


def rotate_proxy_mesh_x(obj, degrees):
    make_mesh_single_user(obj)
    rotation = Matrix.Rotation(math.radians(degrees), 4, "X")
    obj.data.transform(rotation)
    obj.data.update()


def reset_source_objects():
    for obj in mesh_objects_in_collection(SOURCE_COLLECTION_NAME):
        apply_object_rotation_and_scale(obj)
        print(f"Reset source object: {obj.name}")


def reset_proxy_objects():
    for obj in mesh_objects_in_collection(PROXY_COLLECTION_NAME):
        apply_object_rotation_and_scale(obj)
        rotate_proxy_mesh_x(obj, PROXY_X_ROTATION_DEGREES)
        obj.location = (0.0, 0.0, 0.0)
        print(f"Reset and rotated proxy object: {obj.name}")


def main():
    reset_source_objects()
    reset_proxy_objects()
    print(
        f'Finished resetting "{SOURCE_COLLECTION_NAME}" and "{PROXY_COLLECTION_NAME}" '
        f"to object location 0,0,0. Proxy meshes were rotated "
        f"{PROXY_X_ROTATION_DEGREES:g} degrees around X and applied."
    )


main()
