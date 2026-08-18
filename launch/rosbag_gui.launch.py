from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='rosbag_gui',
            executable='rosbag_gui',
            name='rosbag_gui',
            output='screen',
        )
    ])
