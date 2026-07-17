import math
import random
import re

import bpy


SOURCE_COLLECTION_NAME = "Objects"
EXCLUDED_COLLECTION_NAME = "Excluded SliceObjects"
OBJECT_NAME_PATTERN = re.compile(r"^SliceObject_(\d+)(?:\.\d+)?$")
EXCLUDED_INDICES = {2, 8, 12, 18}
CIRCLE_RADIUS = 6.0
CENTER_X = 0.0
CENTER_Y = 0.0
CENTER_Z = 0.0
HIDE_EXCLUDED = True
RANDOM_SEED = None


def collection_named(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def slice_index(obj):
    match = OBJECT_NAME_PATTERN.fullmatch(obj.name)
    if match is None:
        return None
    return int(match.group(1))


def slice_objects(collection):
    objects = []
    for obj in collection.objects:
        index = slice_index(obj)
        if index is not None and obj.type == "MESH":
            objects.append((index, obj))
    return sorted(objects, key=lambda item: item[0])


def link_only_to_collection(obj, target_collection):
    if obj.name not in target_collection.objects.keys():
        target_collection.objects.link(obj)

    for collection in tuple(obj.users_collection):
        if collection != target_collection:
            collection.objects.unlink(obj)


def split_objects(objects, objects_collection, excluded_collection):
    kept = []

    for index, obj in objects:
        if index in EXCLUDED_INDICES:
            link_only_to_collection(obj, excluded_collection)
            obj.hide_viewport = HIDE_EXCLUDED
            obj.hide_render = HIDE_EXCLUDED
            print(f"Excluded {obj.name}")
        else:
            link_only_to_collection(obj, objects_collection)
            obj.hide_viewport = False
            obj.hide_render = False
            kept.append((index, obj))

    return kept


def arrange_circle(objects):
    count = len(objects)
    if count == 0:
        print("No SliceObject_xx mesh objects to arrange")
        return

    positions = list(range(count))
    random.Random(RANDOM_SEED).shuffle(positions)

    for position, (index, obj) in enumerate(objects):
        slot = positions[position]
        angle = math.tau * slot / count
        obj.location.x = CENTER_X + (math.cos(angle) * CIRCLE_RADIUS)
        obj.location.y = CENTER_Y
        obj.location.z = CENTER_Z + (math.sin(angle) * CIRCLE_RADIUS)

        print(
            f"{position:02d}: {obj.name} "
            f"index={index:02d} "
            f"slot={slot:02d} "
            f"loc=({obj.location.x:.3f}, {obj.location.y:.3f}, {obj.location.z:.3f}) "
            "rotation unchanged"
        )


def main():
    objects_collection = bpy.data.collections.get(SOURCE_COLLECTION_NAME)
    if objects_collection is None:
        raise RuntimeError(f'Collection "{SOURCE_COLLECTION_NAME}" not found')

    excluded_collection = collection_named(EXCLUDED_COLLECTION_NAME)
    objects = slice_objects(objects_collection)
    kept = split_objects(objects, objects_collection, excluded_collection)

    if len(kept) != 16:
        print(f"Warning: expected 16 kept objects, found {len(kept)}")

    arrange_circle(kept)
    print("Finished SliceObject circle arrangement")


main()
