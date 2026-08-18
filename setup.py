from setuptools import find_packages, setup

package_name = 'rosbag_gui'

setup(
    name=package_name,
    version='2.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/rosbag_gui.launch.py']),
    ],
    install_requires=['setuptools', 'PyYAML'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@example.com',
    description='ROS 2 GUI tool for rosbag2 record, playback, CSV conversion, and presets.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'rosbag_gui = rosbag_gui.gui:main',
        ],
    },
)
