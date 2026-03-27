# rosbag_gui

ROS1 Noetic 用の rosbag GUI パッケージです。

## 修正版
- 起動時の IndentationError を修正
- 保存先は `rosbag_gui/rosbag/`
- `--keep-alive` をデフォルト ON
- 事前 advertise 機能は削除

## 依存
```bash
sudo apt install python3-pyqt5 python3-rospkg python3-yaml
sudo apt install ros-noetic-rosbag ros-noetic-rostopic
```

## ビルド
```bash
cd ~/catkin_ws/src
unzip rosbag_gui_ros1_fixed.zip
cd ..
catkin_make
source devel/setup.bash
```

## 起動
```bash
roslaunch rosbag_gui rosbag_gui.launch
```
