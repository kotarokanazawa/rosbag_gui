# rosbag_gui (ROS 2)

ROS 2 / rosbag2 用GUIパッケージです。

主な機能:
- 現在のTopic一覧から選択して rosbag2 を保存
- Topic選択プリセット
- rosbag2 の情報表示・Topic選択再生・開始位置指定・ループ・Remap
- rosbag2 からTopicごとにCSV出力
- zstd圧縮したbagへの変換

## 対象

ROS 2 Humble / Jazzy 系を想定しています。
ROS 2ではbagは通常1個の `.bag` ファイルではなく、`metadata.yaml` を含むディレクトリとして扱います。

## 依存パッケージ

Ubuntu / ROS 2 Humbleの例:

```bash
sudo apt update
sudo apt install python3-pyqt5 python3-yaml \
  ros-humble-ros2bag \
  ros-humble-rosbag2-py \
  ros-humble-rosbag2-compression \
  ros-humble-rosidl-runtime-py
```

Jazzyの場合は `humble` を `jazzy` に置き換えてください。

## ビルド

```bash
cd ~/ros2_ws/src
# rosbag_gui_ros2 をこのディレクトリに配置
cd ~/ros2_ws
colcon build --symlink-install --packages-select rosbag_gui
source install/setup.bash
```

## 起動

```bash
ros2 run rosbag_gui rosbag_gui
```

または

```bash
ros2 launch rosbag_gui rosbag_gui.launch.py
```

## 保存場所

デフォルトのbag保存先:

```text
~/rosbag
```

Topicプリセット:

```text
~/.config/rosbag_gui/presets
```

## ROS 1版からの主な変更

- `catkin` → `ament_python`
- `rospy` → `rclpy`
- `rosbag` → `ros2 bag`
- `rostopic`依存を削除
- `.bag`ファイル選択 → rosbag2ディレクトリ選択
- ROS 1のbz2/lz4圧縮 → rosbag2のzstd圧縮
- CSV変換は `rosbag2_py` でbagを直接読み込む方式
