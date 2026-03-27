# rosbag_gui

ROS1 Noetic 用の rosbag GUI パッケージです。
GUIからの保存，Topic選別，プリセット保存，再生，シークバー付き，CSV変換

## 依存
```bash
sudo apt install python3-pyqt5 python3-rospkg python3-yaml
sudo apt install ros-noetic-rosbag ros-noetic-rostopic
```

## ビルド
```bash
cd ~/catkin_ws/src
clone this repository
cd ..
catkin_make
source devel/setup.bash
```

## 起動
```bash
roslaunch rosbag_gui rosbag_gui.launch
```
