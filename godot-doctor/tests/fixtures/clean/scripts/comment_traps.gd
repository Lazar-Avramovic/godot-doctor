extends Node

# preload("res://missing_from_comment.tres")
# load("res://also_missing_from_comment.tres")

const TEXT = "preload('res://missing_from_string.tres')"


func _ready() -> void:
    print(TEXT)
