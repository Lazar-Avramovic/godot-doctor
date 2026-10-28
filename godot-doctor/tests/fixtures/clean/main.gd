extends Node2D

const PlayerScene = preload("res://player.tscn")


func _ready() -> void:
    var stats = load("res://data/player_stats.tres")
    if stats == null:
        push_error("missing stats")
