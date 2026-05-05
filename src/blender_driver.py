"""
Blender 驱动脚本

本脚本在 Blender Python 环境中执行（通过 blender --background --python 调用）。
功能：加载 FBX 模型、设置场景（摄像机、灯光、透明背景）、渲染输出。
"""

import os
import sys
import math

import bpy


def clear_scene():
    """清空默认场景中的所有对象。"""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    for collection in bpy.data.collections:
        bpy.data.collections.remove(collection)


def import_fbx(fbx_path: str):
    """导入 FBX 模型并返回导入的 Armature 对象。"""
    bpy.ops.import_scene.fbx(filepath=fbx_path)

    armature = None
    for obj in bpy.context.selected_objects:
        if obj.type == "ARMATURE":
            armature = obj
            break

    if armature:
        bpy.context.view_layer.objects.active = armature

    return armature


def setup_camera(target_obj=None):
    """设置摄像机：自动根据模型尺寸调整构图。"""
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 50
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    # 自动计算模型的包围盒，确定合适的摄像机位置
    if target_obj:
        from mathutils import Vector

        min_z, max_z = float("inf"), float("-inf")
        for child in target_obj.children:
            if child.type == "MESH":
                for corner in child.bound_box:
                    world_co = child.matrix_world @ Vector(corner)
                    min_z = min(min_z, world_co.z)
                    max_z = max(max_z, world_co.z)

        if min_z == float("inf"):
            min_z, max_z = 0, 2.0

        model_height = max_z - min_z
        look_at_z = min_z + model_height * 0.65
        cam_distance = model_height * 1.6

        print(f"  模型高度: {model_height:.2f}m, 看向: z={look_at_z:.2f}")
    else:
        look_at_z = 1.5
        cam_distance = 3.0

    cam_obj.location = (0, -cam_distance, look_at_z + 0.2)
    cam_obj.rotation_euler = (math.radians(90), 0, 0)

    if target_obj:
        # 创建一个空物体作为摄像机注视目标
        empty = bpy.data.objects.new("CameraTarget", None)
        empty.location = (0, 0, look_at_z)
        bpy.context.scene.collection.objects.link(empty)

        constraint = cam_obj.constraints.new(type="TRACK_TO")
        constraint.target = empty
        constraint.track_axis = "TRACK_NEGATIVE_Z"
        constraint.up_axis = "UP_Y"

    return cam_obj


def setup_lighting():
    """设置三点光照。"""
    # 主光（Key Light）
    key_data = bpy.data.lights.new("KeyLight", type="AREA")
    key_data.energy = 200
    key_data.size = 3
    key_obj = bpy.data.objects.new("KeyLight", key_data)
    key_obj.location = (2, -2, 3)
    key_obj.rotation_euler = (math.radians(45), 0, math.radians(30))
    bpy.context.scene.collection.objects.link(key_obj)

    # 补光（Fill Light）
    fill_data = bpy.data.lights.new("FillLight", type="AREA")
    fill_data.energy = 80
    fill_data.size = 2
    fill_obj = bpy.data.objects.new("FillLight", fill_data)
    fill_obj.location = (-2, -2, 2)
    fill_obj.rotation_euler = (math.radians(45), 0, math.radians(-30))
    bpy.context.scene.collection.objects.link(fill_obj)

    # 背光（Rim Light）
    rim_data = bpy.data.lights.new("RimLight", type="AREA")
    rim_data.energy = 100
    rim_data.size = 2
    rim_obj = bpy.data.objects.new("RimLight", rim_data)
    rim_obj.location = (0, 2, 3)
    rim_obj.rotation_euler = (math.radians(-45), 0, 0)
    bpy.context.scene.collection.objects.link(rim_obj)


def setup_render(width: int = 480, height: int = 480, transparent: bool = True):
    """配置渲染参数。"""
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"

    if transparent:
        scene.render.film_transparent = True
        scene.render.image_settings.color_mode = "RGBA"

    scene.render.fps = 30


def load_animation(armature, anim_fbx_path: str):
    """从独立 FBX 文件加载动画并应用到 armature。"""
    anim_fbx_path = os.path.abspath(anim_fbx_path)
    if not os.path.exists(anim_fbx_path):
        print(f"警告: 动画文件不存在 {anim_fbx_path}")
        return

    # 记住当前对象集合
    existing_objects = set(bpy.data.objects.keys())

    bpy.ops.import_scene.fbx(filepath=anim_fbx_path)

    # 找到新导入的 armature（动画数据源）
    anim_armature = None
    new_objects = []
    for obj in bpy.data.objects:
        if obj.name not in existing_objects:
            new_objects.append(obj)
            if obj.type == "ARMATURE":
                anim_armature = obj

    if anim_armature and anim_armature.animation_data:
        action = anim_armature.animation_data.action
        if action:
            if not armature.animation_data:
                armature.animation_data_create()
            armature.animation_data.action = action
            print(f"  动画已应用: {action.name} ({action.frame_range[0]:.0f}~{action.frame_range[1]:.0f} 帧)")

    # 删除导入的多余对象（保留动画数据）
    for obj in new_objects:
        if obj != armature:
            bpy.data.objects.remove(obj, do_unlink=True)


def setup_animation(armature, anim_path: str, fps: int = 30, duration_s: float = 3.0):
    """设置动画：加载 Mixamo 动画 + 设置帧范围。"""
    load_animation(armature, anim_path)

    total_frames = int(fps * duration_s)
    scene = bpy.context.scene
    scene.frame_start = 1

    if armature.animation_data and armature.animation_data.action:
        action = armature.animation_data.action
        anim_frames = int(action.frame_range[1] - action.frame_range[0])
        if anim_frames > 0 and total_frames > anim_frames:
            scene.frame_end = total_frames
            # 循环动画
            for fcurve in action.fcurves:
                mod = fcurve.modifiers.new(type="CYCLES")
        else:
            scene.frame_end = min(total_frames, anim_frames)
    else:
        scene.frame_end = total_frames

    print(f"  帧范围: {scene.frame_start} ~ {scene.frame_end}")


def render_frame(output_path: str, frame: int = 1):
    """渲染单帧并保存。"""
    bpy.context.scene.frame_set(frame)
    bpy.context.scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)
    print(f"渲染完成: {output_path}")


def render_animation(output_dir: str, frame_start: int = 1, frame_end: int = 90):
    """渲染帧序列动画。"""
    scene = bpy.context.scene
    scene.frame_start = frame_start
    scene.frame_end = frame_end
    scene.render.filepath = os.path.join(output_dir, "frame_")
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(animation=True)
    print(f"动画渲染完成: {output_dir} ({frame_end - frame_start + 1} 帧)")


def parse_args():
    """解析命令行参数。"""
    argv = sys.argv
    separator_idx = argv.index("--") if "--" in argv else len(argv)
    script_args = argv[separator_idx + 1:]

    args = {
        "fbx_path": script_args[0] if len(script_args) > 0 else "asset/swat.fbx",
        "output_path": script_args[1] if len(script_args) > 1 else "output/avatar/test_frame.png",
        "width": int(script_args[2]) if len(script_args) > 2 else 480,
        "height": int(script_args[3]) if len(script_args) > 3 else 480,
        "mode": script_args[4] if len(script_args) > 4 else "single",
        "duration": float(script_args[5]) if len(script_args) > 5 else 3.0,
        "anim_path": script_args[6] if len(script_args) > 6 else "asset/animations/Talking.fbx",
    }

    args["fbx_path"] = os.path.abspath(args["fbx_path"])
    args["output_path"] = os.path.abspath(args["output_path"])
    args["anim_path"] = os.path.abspath(args["anim_path"])

    return args


def main():
    """主流程：加载模型、设置场景、渲染。"""
    args = parse_args()

    os.makedirs(os.path.dirname(args["output_path"]), exist_ok=True)

    print(f"FBX: {args['fbx_path']}")
    print(f"输出: {args['output_path']}")
    print(f"分辨率: {args['width']}x{args['height']}")
    print(f"模式: {args['mode']}")

    clear_scene()

    armature = import_fbx(args["fbx_path"])
    if armature:
        print(f"模型已导入: {armature.name}")
        print(f"  骨骼数: {len(armature.data.bones)}")
    else:
        print("警告: 未找到 Armature")

    setup_camera(armature)
    setup_lighting()
    setup_render(args["width"], args["height"], transparent=True)

    if args["mode"] == "animation" and armature:
        fps = 30
        duration = args["duration"]
        setup_animation(armature, args["anim_path"], fps=fps, duration_s=duration)
        output_dir = os.path.dirname(args["output_path"])
        render_animation(output_dir, 1, bpy.context.scene.frame_end)
    else:
        render_frame(args["output_path"])


if __name__ == "__main__":
    main()
