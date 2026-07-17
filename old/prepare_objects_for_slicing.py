import re

import bpy


TARGET_COLLECTION_NAME = "Objects"
EXPECTED_OBJECT_COUNT = 20
OBJECT_NAME_PREFIX = "SliceObject"
EXCLUDED_COLLECTION_NAMES = {"Slices"}
EXCLUDED_OBJECT_PREFIXES = ("SliceGN-",)
TRANSFORM_EPSILON = 1.0e-6


def collection_sort_key(collection):
    match = re.fullmatch(r"Collection(?:\s+|\.)(\d+)", collection.name)
    if match:
        return (0, int(match.group(1)))
    if collection.name == "Collection":
        return (0, 0)
    if collection.name == TARGET_COLLECTION_NAME:
        return (1, 0)
    return (2, collection.name.lower())


def object_sort_key(obj):
    collections = sorted(obj.users_collection, key=collection_sort_key)
    primary_collection_key = collection_sort_key(collections[0]) if collections else (9, "")
    return (primary_collection_key, obj.location.y * -1.0, obj.location.x, obj.name.lower())


def should_include_object(obj):
    if obj.type != "MESH":
        return False
    if any(obj.name.startswith(prefix) for prefix in EXCLUDED_OBJECT_PREFIXES):
        return False
    return not any(collection.name in EXCLUDED_COLLECTION_NAMES for collection in obj.users_collection)


def target_collection():
    collection = bpy.data.collections.get(TARGET_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(TARGET_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
    return collection


def unlink_from_other_collections(obj, keep_collection):
    for collection in tuple(obj.users_collection):
        if collection != keep_collection:
            collection.objects.unlink(obj)


def unique_object_name(index, obj):
    base_name = f"{OBJECT_NAME_PREFIX}_{index:02d}"
    existing = bpy.data.objects.get(base_name)
    if existing is None or existing == obj:
        return base_name
    return f"{base_name}_{existing.name_full}"


def warn_about_non_identity_transform(obj):
    has_rotation = any(abs(value) > TRANSFORM_EPSILON for value in obj.rotation_euler)
    has_scale = any(abs(value - 1.0) > TRANSFORM_EPSILON for value in obj.scale)

    if has_rotation or has_scale:
        print(
            f"Warning: {obj.name} has unapplied rotation/scale. "
            "Slicing uses the object's local origin and local axes; "
            "radii are measured in local mesh units."
        )


def prepare_objects():
    collection = target_collection()
    objects = sorted(
        [obj for obj in bpy.context.scene.objects if should_include_object(obj)],
        key=object_sort_key,
    )

    if len(objects) != EXPECTED_OBJECT_COUNT:
        print(
            f"Warning: expected {EXPECTED_OBJECT_COUNT} mesh objects, "
            f"found {len(objects)}"
        )

    for index, obj in enumerate(objects):
        original_name = obj.get("original_name", obj.name)
        obj["original_name"] = original_name
        obj["slice_index"] = index

        if obj.name not in collection.objects.keys():
            collection.objects.link(obj)

        unlink_from_other_collections(obj, collection)
        obj.name = unique_object_name(index, obj)
        warn_about_non_identity_transform(obj)

        if obj.data is not None:
            obj.data.name = f"{obj.name} Mesh"

        print(f"{index:02d}: {obj.name} from {original_name!r}")

    print(f'Prepared {len(objects)} objects in collection "{TARGET_COLLECTION_NAME}"')
    print("OSC object indices follow the zero-padded object names.")


prepare_objects()
