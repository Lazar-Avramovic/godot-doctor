extends Node

const Missing = preload("res://missing_resource.tres")


func _ready() -> void:
    var other = load("res://also_missing.tres")
    if other == null:
        push_warning("missing")
