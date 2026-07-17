"""Create the reusable slot objects required by blender_slice_visualizer.py."""

import bpy


SLOT_COUNT = 20
PARK_LOCATION = (100.0, 0.0, 0.0)

SLOT_PROXY_PREFIX = "SliceSlotProxy"
SLOT_PLANE_PREFIX = "SliceSlotPlane"
SLOT_LOOP_PREFIX = "SliceSlotLoop"

SLOT_PROXY_COLLECTION = "Slice Slot Proxies"
SLOT_PLANE_COLLECTION = "Slice Planes"
SLOT_LOOP_COLLECTION = "Slice Loops"

PLANE_MATERIAL_NAME = "Dynamic Slice Plane Material"
LOOP_MATERIAL_NAME = "Dynamic Slice Loop Material"
LOOP_BEVEL_DEPTH = 0.008


def main() -> None:
    proxy_collection = _collection(SLOT_PROXY_COLLECTION)
    plane_collection = _collection(SLOT_PLANE_COLLECTION)
    loop_collection = _collection(SLOT_LOOP_COLLECTION)
    plane_material = bpy.data.materials[PLANE_MATERIAL_NAME]
    loop_material = bpy.data.materials[LOOP_MATERIAL_NAME]

    for slot_id in range(SLOT_COUNT):
        proxy = _mesh_object(f"{SLOT_PROXY_PREFIX}_{slot_id:02d}", proxy_collection)
        plane = _mesh_object(f"{SLOT_PLANE_PREFIX}_{slot_id:02d}", plane_collection)
        loop = _curve_object(f"{SLOT_LOOP_PREFIX}_{slot_id:02d}", loop_collection)

        if plane.data.users > 1:
            plane.data = plane.data.copy()
        if loop.data.users > 1:
            loop.data = loop.data.copy()
        _assign_material(plane.data, plane_material)
        _assign_material(loop.data, loop_material)
        loop.data.dimensions = "3D"
        loop.data.bevel_depth = LOOP_BEVEL_DEPTH
        loop.data.bevel_resolution = 3

        for obj in (proxy, plane, loop):
            obj.location = PARK_LOCATION
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_set(False)

    print(f"Prepared {SLOT_COUNT} visual slot triples at {PARK_LOCATION}")


def _collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(collection)
    return collection


def _mesh_object(name: str, collection: bpy.types.Collection) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name + " Mesh"))
    if obj.type != "MESH":
        raise TypeError(f"{name!r} exists but is not a mesh")
    _link_object(obj, collection)
    return obj


def _curve_object(name: str, collection: bpy.types.Collection) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None:
        curve = bpy.data.curves.new(name + " Curve", "CURVE")
        obj = bpy.data.objects.new(name, curve)
    if obj.type != "CURVE":
        raise TypeError(f"{name!r} exists but is not a curve")
    _link_object(obj, collection)
    return obj


def _link_object(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def _assign_material(data: object, material: bpy.types.Material) -> None:
    if data.materials:
        data.materials[0] = material
    else:
        data.materials.append(material)


if __name__ == "__main__":
    main()
