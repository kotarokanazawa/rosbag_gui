#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import shlex
import time
import json
import yaml
import signal
import datetime
import subprocess
from pathlib import Path
from typing import List, Optional

import rospy
import rostopic
from PyQt5 import QtWidgets, QtCore

try:
    import rospkg
except Exception:
    rospkg = None


def package_default_save_dir() -> str:
    if rospkg is not None:
        try:
            rp = rospkg.RosPack()
            pkg_path = rp.get_path("rosbag_gui")
            p = os.path.join(pkg_path, "rosbag")
            os.makedirs(p, exist_ok=True)
            return p
        except Exception:
            pass
    p = os.path.expanduser("~/catkin_ws/src/rosbag_gui/rosbag")
    os.makedirs(p, exist_ok=True)
    return p


def package_preset_dir() -> str:
    if rospkg is not None:
        try:
            rp = rospkg.RosPack()
            pkg_path = rp.get_path("rosbag_gui")
            p = os.path.join(pkg_path, "presets")
            os.makedirs(p, exist_ok=True)
            return p
        except Exception:
            pass
    p = os.path.expanduser("~/catkin_ws/src/rosbag_gui/presets")
    os.makedirs(p, exist_ok=True)
    return p


def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(max(n, 0))
    for u in units:
        if v < 1024.0 or u == units[-1]:
            return f"{v:.1f} {u}"
        v /= 1024.0
    return f"{n} B"


def human_bps(n: float) -> str:
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    v = float(max(n, 0))
    for u in units:
        if v < 1024.0 or u == units[-1]:
            return f"{v:.2f} {u}"
        v /= 1024.0
    return f"{n} B/s"


def now_string() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_filename(name: str) -> str:
    s = name.strip().replace("/", "__")
    s = re.sub(r"[^a-zA-Z0-9_.\-]+", "_", s)
    return s or "unnamed"


def quote_join(args: List[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


class TopicRowWidget(QtWidgets.QWidget):
    toggled = QtCore.pyqtSignal()

    def __init__(self, topic_name: str, topic_type: str, parent=None):
        super().__init__(parent)
        self.topic_name = topic_name
        self.topic_type = topic_type

        self.checkbox = QtWidgets.QCheckBox(topic_name)
        self.checkbox.toggled.connect(self.toggled.emit)

        self.type_label = QtWidgets.QLabel(topic_type)
        self.type_label.setMinimumWidth(240)
        self.type_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.type_label.setObjectName("SubtleLabel")

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.addWidget(self.checkbox, 1)
        lay.addWidget(self.type_label, 0)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_checked(self, checked: bool):
        self.checkbox.setChecked(checked)


class RemapRowWidget(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()

    def __init__(self, src_topic: str, parent=None):
        super().__init__(parent)
        self.src_topic = src_topic

        self.src_label = QtWidgets.QLabel(src_topic)
        self.dst_edit = QtWidgets.QLineEdit(src_topic)
        self.remove_btn = QtWidgets.QPushButton("削除")
        self.remove_btn.setFixedWidth(70)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.addWidget(self.src_label, 1)
        lay.addWidget(QtWidgets.QLabel("→"), 0)
        lay.addWidget(self.dst_edit, 1)
        lay.addWidget(self.remove_btn, 0)

        self.dst_edit.textChanged.connect(self.changed.emit)

    def get_rule(self) -> Optional[str]:
        dst = self.dst_edit.text().strip()
        if not dst:
            return None
        return f"{self.src_topic}:={dst}"


class BagInfoWorker(QtCore.QThread):
    result_ready = QtCore.pyqtSignal(dict)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, bag_path: str):
        super().__init__()
        self.bag_path = bag_path

    def run(self):
        try:
            cmd = ["rosbag", "info", "--yaml", self.bag_path]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = yaml.safe_load(res.stdout) or {}
            self.result_ready.emit(info)
        except Exception as e:
            self.error_occurred.emit(str(e))


class CsvConvertWorker(QtCore.QThread):
    log_line = QtCore.pyqtSignal(str)
    finished_ok = QtCore.pyqtSignal(str)
    finished_ng = QtCore.pyqtSignal(str)

    def __init__(self, bag_path: str, out_dir: str, topics: List[str]):
        super().__init__()
        self.bag_path = bag_path
        self.out_dir = out_dir
        self.topics = topics

    def run(self):
        os.makedirs(self.out_dir, exist_ok=True)
        failed = []
        try:
            for i, topic in enumerate(self.topics, 1):
                csv_path = os.path.join(self.out_dir, sanitize_filename(topic) + ".csv")
                cmd = f'rostopic echo -b {shlex.quote(self.bag_path)} -p {shlex.quote(topic)} > {shlex.quote(csv_path)}'
                self.log_line.emit(f"CSV変換 [{i}/{len(self.topics)}] {topic}")
                ret = subprocess.run(cmd, shell=True)
                if ret.returncode != 0:
                    failed.append(topic)
            if failed:
                self.finished_ng.emit("CSV変換は完了しましたが，一部失敗しました:\n" + "\n".join(failed))
            else:
                self.finished_ok.emit(self.out_dir)
        except Exception as e:
            self.finished_ng.emit(str(e))


class BagCompressWorker(QtCore.QThread):
    log_line = QtCore.pyqtSignal(str)
    finished_ok = QtCore.pyqtSignal(str)
    finished_ng = QtCore.pyqtSignal(str)

    def __init__(self, bag_path: str, mode: str):
        super().__init__()
        self.bag_path = bag_path
        self.mode = mode

    def run(self):
        try:
            if self.mode not in ("bz2", "lz4"):
                self.finished_ng.emit("圧縮方式は bz2 または lz4 のみ対応です。")
                return
            cmd = ["rosbag", "compress", f"--{self.mode}", self.bag_path]
            self.log_line.emit("圧縮開始: " + quote_join(cmd))
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.stdout.strip():
                self.log_line.emit(res.stdout.strip())
            if res.returncode != 0:
                self.finished_ng.emit(res.stderr.strip() or "rosbag compress に失敗しました。")
                return
            self.finished_ok.emit(self.bag_path)
        except Exception as e:
            self.finished_ng.emit(str(e))


class RosbagGuiWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("rosbag_gui")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 760)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self.record_proc = None
        self.play_proc = None
        self.current_record_target = None
        self.record_started_at = None
        self.last_record_size = 0
        self.last_record_time = 0.0
        self.play_started_at = None

        self.bag_info_worker = None
        self.csv_worker = None
        self.compress_worker = None
        self.current_bag_info = {}
        self.play_duration_total = 0.0
        self.play_seek_dragging = False

        self.topic_rows = []
        self.play_topic_rows = []
        self.remap_rows = []

        self._build_ui()
        self._apply_style()
        self.refresh_live_topics()
        self.refresh_record_presets()

        self.live_topic_timer = QtCore.QTimer(self)
        self.live_topic_timer.timeout.connect(self._refresh_live_topics_if_idle)
        self.live_topic_timer.start(3000)

        self.runtime_timer = QtCore.QTimer(self)
        self.runtime_timer.timeout.connect(self._update_runtime_status)
        self.runtime_timer.start(500)

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        top_bar = QtWidgets.QFrame()
        top_bar.setObjectName("TopBar")
        tb = QtWidgets.QHBoxLayout(top_bar)
        tb.setContentsMargins(8, 6, 8, 6)

        self.header_state = QtWidgets.QLabel("READY")
        self.header_state.setObjectName("StatePill")

        self.fullscreen_btn = QtWidgets.QPushButton("最大化")
        self.fullscreen_btn.setObjectName("HeaderButton")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        self.exit_btn = QtWidgets.QPushButton("閉じる")
        self.exit_btn.setObjectName("HeaderButton")
        self.exit_btn.clicked.connect(self.close)

        tb.addStretch(1)
        tb.addWidget(self.header_state)
        tb.addWidget(self.fullscreen_btn)
        tb.addWidget(self.exit_btn)
        root.addWidget(top_bar)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        root.addWidget(self.tabs, 1)

        self.tab_record = QtWidgets.QWidget()
        self.tab_play = QtWidgets.QWidget()
        self.tab_convert = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_record, "保存")
        self.tabs.addTab(self.tab_play, "再生")
        self.tabs.addTab(self.tab_convert, "CSV変換")
        QtWidgets.QShortcut(QtCore.Qt.Key_F11, self, activated=self.toggle_fullscreen)
        QtWidgets.QShortcut(QtCore.Qt.Key_Escape, self, activated=self.exit_fullscreen)

        self._build_record_tab()
        self._build_play_tab()
        self._build_convert_tab()

        log_group = QtWidgets.QGroupBox("ログ")
        lv = QtWidgets.QVBoxLayout(log_group)
        self.log_text = QtWidgets.QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(4000)
        self.log_text.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.log_text.setMinimumHeight(140)
        lv.addWidget(self.log_text)
        root.addWidget(log_group, 0)

    def _build_record_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_record)
        layout.setSpacing(10)

        top_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        top_split.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        top_split.setChildrenCollapsible(False)
        layout.addWidget(top_split, 1)

        left = QtWidgets.QWidget()
        left.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left_v = QtWidgets.QVBoxLayout(left)

        right = QtWidgets.QWidget()
        right.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        right_v = QtWidgets.QVBoxLayout(right)

        top_split.addWidget(left)
        top_split.addWidget(right)
        top_split.setSizes([760, 660])

        save_group = QtWidgets.QGroupBox("保存設定")
        save_grid = QtWidgets.QGridLayout(save_group)

        self.rec_dir_edit = QtWidgets.QLineEdit(package_default_save_dir())
        self.rec_dir_btn = QtWidgets.QPushButton("参照")
        self.rec_dir_btn.clicked.connect(self.choose_record_dir)

        self.rec_name_edit = QtWidgets.QLineEdit("record")
        self.rec_stamp_check = QtWidgets.QCheckBox("日時を付与")
        self.rec_stamp_check.setChecked(True)

        self.rec_split_check = QtWidgets.QCheckBox("splitを有効化")
        self.rec_split_size_spin = QtWidgets.QSpinBox()
        self.rec_split_size_spin.setRange(64, 8192)
        self.rec_split_size_spin.setValue(1024)
        self.rec_split_size_spin.setSuffix(" MB")
        self.rec_split_size_spin.setEnabled(False)
        self.rec_split_check.toggled.connect(self.rec_split_size_spin.setEnabled)

        self.rec_none_radio = QtWidgets.QRadioButton("なし")
        self.rec_bz2_radio = QtWidgets.QRadioButton("bz2")
        self.rec_lz4_radio = QtWidgets.QRadioButton("lz4")
        self.rec_none_radio.setChecked(True)

        self.rec_buff_spin = QtWidgets.QSpinBox()
        self.rec_buff_spin.setRange(1, 4096)
        self.rec_buff_spin.setValue(256)
        self.rec_buff_spin.setSuffix(" MB")

        self.rec_chunk_spin = QtWidgets.QSpinBox()
        self.rec_chunk_spin.setRange(64, 40960)
        self.rec_chunk_spin.setValue(768)
        self.rec_chunk_spin.setSuffix(" KB")

        save_grid.addWidget(QtWidgets.QLabel("保存先"), 0, 0)
        save_grid.addWidget(self.rec_dir_edit, 0, 1)
        save_grid.addWidget(self.rec_dir_btn, 0, 2)
        save_grid.addWidget(QtWidgets.QLabel("ファイル名"), 1, 0)
        save_grid.addWidget(self.rec_name_edit, 1, 1)
        save_grid.addWidget(self.rec_stamp_check, 1, 2)
        save_grid.addWidget(self.rec_split_check, 2, 0)
        save_grid.addWidget(self.rec_split_size_spin, 2, 1)
        save_grid.addWidget(QtWidgets.QLabel("buffer"), 3, 0)
        save_grid.addWidget(self.rec_buff_spin, 3, 1)
        save_grid.addWidget(QtWidgets.QLabel("chunk"), 3, 2)
        save_grid.addWidget(self.rec_chunk_spin, 3, 3)

        comp = QtWidgets.QHBoxLayout()
        comp.addWidget(QtWidgets.QLabel("圧縮"))
        comp.addWidget(self.rec_none_radio)
        comp.addWidget(self.rec_bz2_radio)
        comp.addWidget(self.rec_lz4_radio)
        comp.addStretch(1)
        save_grid.addLayout(comp, 4, 0, 1, 4)
        left_v.addWidget(save_group)

        preset_group = QtWidgets.QGroupBox("保存プリセット")
        pgp = QtWidgets.QGridLayout(preset_group)
        self.rec_preset_combo = QtWidgets.QComboBox()
        self.rec_preset_refresh_btn = QtWidgets.QPushButton("更新")
        self.rec_preset_refresh_btn.clicked.connect(self.refresh_record_presets)

        self.rec_preset_name_edit = QtWidgets.QLineEdit()
        self.rec_preset_name_edit.setPlaceholderText("プリセット名")
        self.rec_preset_save_btn = QtWidgets.QPushButton("現在の選択を保存")
        self.rec_preset_load_btn = QtWidgets.QPushButton("読み込み")
        self.rec_preset_delete_btn = QtWidgets.QPushButton("削除")
        self.rec_preset_save_btn.clicked.connect(self.save_record_preset)
        self.rec_preset_load_btn.clicked.connect(self.load_selected_record_preset)
        self.rec_preset_delete_btn.clicked.connect(self.delete_selected_record_preset)

        pgp.addWidget(QtWidgets.QLabel("保存済み"), 0, 0)
        pgp.addWidget(self.rec_preset_combo, 0, 1)
        pgp.addWidget(self.rec_preset_refresh_btn, 0, 2)
        pgp.addWidget(QtWidgets.QLabel("新規名"), 1, 0)
        pgp.addWidget(self.rec_preset_name_edit, 1, 1, 1, 2)
        pgp.addWidget(self.rec_preset_save_btn, 2, 0)
        pgp.addWidget(self.rec_preset_load_btn, 2, 1)
        pgp.addWidget(self.rec_preset_delete_btn, 2, 2)
        left_v.addWidget(preset_group)

        state_group = QtWidgets.QGroupBox("保存状態")
        sg = QtWidgets.QGridLayout(state_group)
        self.rec_start_btn = QtWidgets.QPushButton("保存開始")
        self.rec_stop_btn = QtWidgets.QPushButton("保存停止")
        self.rec_stop_btn.setEnabled(False)
        self.rec_start_btn.clicked.connect(self.start_recording)
        self.rec_stop_btn.clicked.connect(self.stop_recording)

        self.rec_status_label = QtWidgets.QLabel("待機中")
        self.rec_elapsed_label = QtWidgets.QLabel("0.0 s")
        self.rec_target_label = QtWidgets.QLabel("-")
        self.rec_target_label.setWordWrap(True)
        self.rec_size_label = QtWidgets.QLabel("-")
        self.rec_rate_label = QtWidgets.QLabel("-")
        self.rec_progress = QtWidgets.QProgressBar()
        self.rec_progress.setRange(0, 0)
        self.rec_progress.setVisible(False)
        self.rec_progress.setFormat("SAVE")

        sg.addWidget(self.rec_start_btn, 0, 0)
        sg.addWidget(self.rec_stop_btn, 0, 1)
        sg.addWidget(self.rec_progress, 0, 2, 1, 2)
        sg.addWidget(QtWidgets.QLabel("状態"), 1, 0)
        sg.addWidget(self.rec_status_label, 1, 1)
        sg.addWidget(QtWidgets.QLabel("経過時間"), 1, 2)
        sg.addWidget(self.rec_elapsed_label, 1, 3)
        sg.addWidget(QtWidgets.QLabel("保存先"), 2, 0)
        sg.addWidget(self.rec_target_label, 2, 1, 1, 3)
        sg.addWidget(QtWidgets.QLabel("容量"), 3, 0)
        sg.addWidget(self.rec_size_label, 3, 1)
        sg.addWidget(QtWidgets.QLabel("保存速度"), 3, 2)
        sg.addWidget(self.rec_rate_label, 3, 3)
        left_v.addWidget(state_group)
        left_v.addStretch(1)

        topic_group = QtWidgets.QGroupBox("現在流れているTopic")
        tv = QtWidgets.QVBoxLayout(topic_group)
        tools = QtWidgets.QHBoxLayout()
        self.rec_refresh_btn = QtWidgets.QPushButton("更新")
        self.rec_refresh_btn.clicked.connect(self.refresh_live_topics)
        self.rec_select_all_btn = QtWidgets.QPushButton("すべて選択")
        self.rec_select_all_btn.clicked.connect(lambda: self._set_visible_topic_checks(self.topic_rows, True))
        self.rec_clear_btn = QtWidgets.QPushButton("すべて解除")
        self.rec_clear_btn.clicked.connect(lambda: self._set_all_topic_checks(self.topic_rows, False))
        self.rec_hide_system_check = QtWidgets.QCheckBox("/rosout /tf /tf_static を隠す")
        self.rec_filter_edit = QtWidgets.QLineEdit()
        self.rec_filter_edit.setPlaceholderText("topic名でフィルタ")
        self.rec_filter_edit.textChanged.connect(self.filter_record_topics)
        self.rec_hide_system_check.toggled.connect(self.filter_record_topics)
        tools.addWidget(self.rec_refresh_btn)
        tools.addWidget(self.rec_select_all_btn)
        tools.addWidget(self.rec_clear_btn)
        tools.addWidget(self.rec_hide_system_check)
        tools.addWidget(self.rec_filter_edit, 1)
        tv.addLayout(tools)

        self.rec_topic_scroll = QtWidgets.QScrollArea()
        self.rec_topic_scroll.setWidgetResizable(True)
        self.rec_topic_container = QtWidgets.QWidget()
        self.rec_topic_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.rec_topic_layout = QtWidgets.QVBoxLayout(self.rec_topic_container)
        self.rec_topic_layout.setContentsMargins(6, 6, 6, 6)
        self.rec_topic_layout.setSpacing(2)
        self.rec_topic_layout.addStretch(1)
        self.rec_topic_scroll.setWidget(self.rec_topic_container)
        tv.addWidget(self.rec_topic_scroll, 1)
        right_v.addWidget(topic_group, 1)

    def _build_play_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_play)
        layout.setSpacing(10)

        file_group = QtWidgets.QGroupBox("再生対象")
        fg = QtWidgets.QGridLayout(file_group)
        self.play_bag_edit = QtWidgets.QLineEdit()
        self.play_bag_btn = QtWidgets.QPushButton("bag選択")
        self.play_read_btn = QtWidgets.QPushButton("bag情報を読む")
        self.play_bag_btn.clicked.connect(self.choose_play_bag)
        self.play_read_btn.clicked.connect(self.load_play_bag_info)
        self.play_info_label = QtWidgets.QLabel("-")
        self.play_info_label.setWordWrap(True)

        fg.addWidget(QtWidgets.QLabel("bagファイル"), 0, 0)
        fg.addWidget(self.play_bag_edit, 0, 1)
        fg.addWidget(self.play_bag_btn, 0, 2)
        fg.addWidget(self.play_read_btn, 0, 3)
        fg.addWidget(self.play_info_label, 1, 0, 1, 4)
        layout.addWidget(file_group)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        left = QtWidgets.QGroupBox("bag内Topic選択")
        left_v = QtWidgets.QVBoxLayout(left)
        tl = QtWidgets.QHBoxLayout()
        self.play_select_all_btn = QtWidgets.QPushButton("すべて選択")
        self.play_clear_btn = QtWidgets.QPushButton("すべて解除")
        self.play_filter_edit = QtWidgets.QLineEdit()
        self.play_filter_edit.setPlaceholderText("topic名でフィルタ")
        self.play_select_all_btn.clicked.connect(lambda: self._set_visible_topic_checks(self.play_topic_rows, True))
        self.play_clear_btn.clicked.connect(lambda: self._set_all_topic_checks(self.play_topic_rows, False))
        self.play_filter_edit.textChanged.connect(self.filter_play_topics)
        tl.addWidget(self.play_select_all_btn)
        tl.addWidget(self.play_clear_btn)
        tl.addWidget(self.play_filter_edit, 1)
        left_v.addLayout(tl)

        self.play_topic_scroll = QtWidgets.QScrollArea()
        self.play_topic_scroll.setWidgetResizable(True)
        self.play_topic_container = QtWidgets.QWidget()
        self.play_topic_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.play_topic_layout = QtWidgets.QVBoxLayout(self.play_topic_container)
        self.play_topic_layout.setContentsMargins(6, 6, 6, 6)
        self.play_topic_layout.setSpacing(2)
        self.play_topic_layout.addStretch(1)
        self.play_topic_scroll.setWidget(self.play_topic_container)
        left_v.addWidget(self.play_topic_scroll, 1)

        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        right_scroll.setMinimumWidth(420)

        right = QtWidgets.QGroupBox("再生オプション")
        right.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
        right_v = QtWidgets.QVBoxLayout(right)

        og = QtWidgets.QGridLayout()
        self.play_rate_spin = QtWidgets.QDoubleSpinBox()
        self.play_rate_spin.setRange(0.01, 100.0)
        self.play_rate_spin.setDecimals(2)
        self.play_rate_spin.setValue(1.0)
        self.play_rate_spin.valueChanged.connect(self._update_play_command_preview)

        self.play_start_spin = QtWidgets.QDoubleSpinBox()
        self.play_start_spin.setRange(0.0, 1e9)
        self.play_start_spin.setDecimals(2)
        self.play_start_spin.setSuffix(" s")
        self.play_start_spin.valueChanged.connect(self.on_play_start_spin_changed)

        self.play_duration_spin = QtWidgets.QDoubleSpinBox()
        self.play_duration_spin.setRange(0.0, 1e9)
        self.play_duration_spin.setDecimals(2)
        self.play_duration_spin.setSuffix(" s (0=無効)")
        self.play_duration_spin.valueChanged.connect(self._update_play_command_preview)

        self.play_delay_spin = QtWidgets.QDoubleSpinBox()
        self.play_delay_spin.setRange(0.0, 100.0)
        self.play_delay_spin.setDecimals(2)
        self.play_delay_spin.setValue(0.2)
        self.play_delay_spin.setSuffix(" s")
        self.play_delay_spin.valueChanged.connect(self._update_play_command_preview)

        self.play_queue_spin = QtWidgets.QSpinBox()
        self.play_queue_spin.setRange(1, 10000)
        self.play_queue_spin.setValue(100)
        self.play_queue_spin.valueChanged.connect(self._update_play_command_preview)

        og.addWidget(QtWidgets.QLabel("再生倍率"), 0, 0)
        og.addWidget(self.play_rate_spin, 0, 1)
        og.addWidget(QtWidgets.QLabel("開始オフセット"), 1, 0)
        og.addWidget(self.play_start_spin, 1, 1)
        og.addWidget(QtWidgets.QLabel("再生時間"), 2, 0)
        og.addWidget(self.play_duration_spin, 2, 1)
        og.addWidget(QtWidgets.QLabel("開始遅延"), 3, 0)
        og.addWidget(self.play_delay_spin, 3, 1)
        og.addWidget(QtWidgets.QLabel("queue size"), 4, 0)
        og.addWidget(self.play_queue_spin, 4, 1)
        right_v.addLayout(og)

        self.play_clock_check = QtWidgets.QCheckBox("--clock")
        self.play_clock_check.setChecked(True)
        self.play_clock_check.toggled.connect(self._update_play_command_preview)

        self.play_use_sim_time_check = QtWidgets.QCheckBox("/use_sim_time を自動切替")
        self.play_use_sim_time_check.setChecked(True)

        self.play_pause_check = QtWidgets.QCheckBox("開始時 pause")
        self.play_pause_check.toggled.connect(self._update_play_command_preview)

        self.play_loop_check = QtWidgets.QCheckBox("ループ再生")
        self.play_loop_check.toggled.connect(self._update_play_command_preview)

        self.play_keep_alive_check = QtWidgets.QCheckBox("--keep-alive")
        self.play_keep_alive_check.setChecked(True)
        self.play_keep_alive_check.toggled.connect(self._update_play_command_preview)

        self.play_wait_sub_check = QtWidgets.QCheckBox("--wait-for-subscribers")
        self.play_wait_sub_check.toggled.connect(self._update_play_command_preview)

        self.play_quiet_check = QtWidgets.QCheckBox("--quiet")
        self.play_quiet_check.toggled.connect(self._update_play_command_preview)

        flags = QtWidgets.QGridLayout()
        flags.setHorizontalSpacing(18)
        flags.setVerticalSpacing(6)
        flags.addWidget(self.play_clock_check, 0, 0)
        flags.addWidget(self.play_use_sim_time_check, 0, 1)
        flags.addWidget(self.play_pause_check, 1, 0)
        flags.addWidget(self.play_loop_check, 1, 1)
        flags.addWidget(self.play_keep_alive_check, 2, 0)
        flags.addWidget(self.play_wait_sub_check, 2, 1)
        flags.addWidget(self.play_quiet_check, 3, 0)
        right_v.addLayout(flags)

        remap_box = QtWidgets.QGroupBox("Remap")
        rv = QtWidgets.QVBoxLayout(remap_box)
        rb = QtWidgets.QHBoxLayout()
        self.remap_add_from_selected_btn = QtWidgets.QPushButton("選択Topicから追加")
        self.remap_clear_btn = QtWidgets.QPushButton("全remap削除")
        self.remap_add_from_selected_btn.clicked.connect(self.add_remap_rows_from_selected)
        self.remap_clear_btn.clicked.connect(self.clear_remap_rows)
        rb.addWidget(self.remap_add_from_selected_btn)
        rb.addWidget(self.remap_clear_btn)
        rb.addStretch(1)
        rv.addLayout(rb)

        self.remap_scroll = QtWidgets.QScrollArea()
        self.remap_scroll.setWidgetResizable(True)
        self.remap_container = QtWidgets.QWidget()
        self.remap_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.remap_layout = QtWidgets.QVBoxLayout(self.remap_container)
        self.remap_layout.setContentsMargins(4, 4, 4, 4)
        self.remap_layout.setSpacing(2)
        self.remap_layout.addStretch(1)
        self.remap_scroll.setWidget(self.remap_container)
        rv.addWidget(self.remap_scroll, 1)
        right_v.addWidget(remap_box, 1)

        right_scroll.setWidget(right)

        splitter.addWidget(left)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([760, 520])

        play_state = QtWidgets.QGroupBox("再生状態")
        pg = QtWidgets.QGridLayout(play_state)
        self.play_start_btn = QtWidgets.QPushButton("再生開始")
        self.play_stop_btn = QtWidgets.QPushButton("再生停止")
        self.play_stop_btn.setEnabled(False)
        self.play_start_btn.clicked.connect(self.start_playback)
        self.play_stop_btn.clicked.connect(self.stop_playback)

        self.play_status_label = QtWidgets.QLabel("待機中")
        self.play_elapsed_label = QtWidgets.QLabel("0.0 s")
        self.play_seek_label = QtWidgets.QLabel("0:00.0 / 0:00.0")

        self.play_seek_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.play_seek_slider.setRange(0, 1000)
        self.play_seek_slider.sliderPressed.connect(self.on_play_seek_pressed)
        self.play_seek_slider.sliderReleased.connect(self.on_play_seek_released)
        self.play_seek_slider.valueChanged.connect(self.on_play_seek_changed)

        self.play_cmd_preview = QtWidgets.QPlainTextEdit()
        self.play_cmd_preview.setReadOnly(True)
        self.play_cmd_preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.play_cmd_preview.setMinimumHeight(90)

        pg.addWidget(self.play_start_btn, 0, 0)
        pg.addWidget(self.play_stop_btn, 0, 1)
        pg.addWidget(QtWidgets.QLabel("状態"), 1, 0)
        pg.addWidget(self.play_status_label, 1, 1)
        pg.addWidget(QtWidgets.QLabel("経過時間"), 1, 2)
        pg.addWidget(self.play_elapsed_label, 1, 3)
        pg.addWidget(QtWidgets.QLabel("時刻シーク"), 2, 0)
        pg.addWidget(self.play_seek_slider, 2, 1, 1, 2)
        pg.addWidget(self.play_seek_label, 2, 3)
        pg.addWidget(QtWidgets.QLabel("コマンド"), 3, 0)
        pg.addWidget(self.play_cmd_preview, 3, 1, 1, 3)
        layout.addWidget(play_state)

    def _build_convert_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_convert)

        group = QtWidgets.QGroupBox("CSV変換")
        g = QtWidgets.QGridLayout(group)
        self.csv_bag_edit = QtWidgets.QLineEdit()
        self.csv_bag_btn = QtWidgets.QPushButton("bag選択")
        self.csv_scan_btn = QtWidgets.QPushButton("bag内Topicを読む")
        self.csv_out_dir_edit = QtWidgets.QLineEdit(package_default_save_dir())
        self.csv_out_dir_btn = QtWidgets.QPushButton("出力先")
        self.csv_topic_list = QtWidgets.QListWidget()
        self.csv_topic_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.csv_all_btn = QtWidgets.QPushButton("すべて選択")
        self.csv_none_btn = QtWidgets.QPushButton("すべて解除")
        self.csv_convert_btn = QtWidgets.QPushButton("CSV変換実行")
        self.bag_compress_btn = QtWidgets.QPushButton("bag圧縮実行")
        self.bag_compress_mode_combo = QtWidgets.QComboBox()
        self.bag_compress_mode_combo.addItems(["lz4", "bz2"])

        self.csv_bag_btn.clicked.connect(self.choose_csv_bag)
        self.csv_scan_btn.clicked.connect(self.load_csv_topics_from_bag)
        self.csv_out_dir_btn.clicked.connect(self.choose_csv_out_dir)
        self.csv_all_btn.clicked.connect(lambda: self._set_listwidget_checks(self.csv_topic_list, True))
        self.csv_none_btn.clicked.connect(lambda: self._set_listwidget_checks(self.csv_topic_list, False))
        self.csv_convert_btn.clicked.connect(self.convert_bag_to_csv)
        self.bag_compress_btn.clicked.connect(self.compress_existing_bag)

        g.addWidget(QtWidgets.QLabel("bagファイル"), 0, 0)
        g.addWidget(self.csv_bag_edit, 0, 1)
        g.addWidget(self.csv_bag_btn, 0, 2)
        g.addWidget(self.csv_scan_btn, 0, 3)
        g.addWidget(QtWidgets.QLabel("出力先"), 1, 0)
        g.addWidget(self.csv_out_dir_edit, 1, 1)
        g.addWidget(self.csv_out_dir_btn, 1, 2)

        btn = QtWidgets.QHBoxLayout()
        btn.addWidget(self.csv_all_btn)
        btn.addWidget(self.csv_none_btn)
        btn.addStretch(1)
        btn.addWidget(QtWidgets.QLabel("圧縮"))
        btn.addWidget(self.bag_compress_mode_combo)
        btn.addWidget(self.bag_compress_btn)
        btn.addWidget(self.csv_convert_btn)

        layout.addWidget(group)
        layout.addLayout(btn)
        layout.addWidget(self.csv_topic_list, 1)

    def _apply_responsive_fonts(self):
        w = max(900, self.width())
        h = max(700, self.height())

        base = 11
        if w >= 1700 or h >= 1100:
            base = 14
        elif w >= 1450 or h >= 950:
            base = 13
        elif w >= 1200 or h >= 820:
            base = 12

        small = max(10, base - 1)
        button = base
        title = base + 1

        self.setStyleSheet(f"""
        QWidget {{
            background: #090c11;
            color: #eef4ff;
            font-size: {base}px;
        }}
        QMainWindow, QScrollArea, QListWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QTabWidget::pane {{
            background: #0b1017;
        }}
        QGroupBox {{
            border: 1px solid #263241;
            border-radius: 14px;
            margin-top: 12px;
            padding-top: 14px;
            background: #111821;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
        }}
        QFrame#TopBar {{
            border: 1px solid #223244;
            border-radius: 12px;
            background: #0f1720;
        }}
        QLabel#StatePill {{
            border: 1px solid #2b80ff;
            border-radius: 14px;
            padding: 5px 10px;
            color: #cfe5ff;
            background: rgba(47,135,255,0.18);
            font-weight: 700;
            font-size: {small}px;
        }}
        QLabel#SubtleLabel {{
            color: #92a5bf;
            font-size: {small}px;
        }}
        QPushButton {{
            background: #182331;
            border: 1px solid #35506d;
            border-radius: 10px;
            padding: 6px 12px;
            font-size: {button}px;
        }}
        QPushButton:hover {{
            background: #203043;
            border: 1px solid #4d8fff;
        }}
        QPushButton:pressed {{
            background: #11253b;
        }}
        QPushButton:disabled {{
            background: #141920;
            color: #6d7d92;
            border: 1px solid #29323d;
        }}
        QPushButton#HeaderButton {{
            background: #13202e;
            border: 1px solid #3f74b6;
            border-radius: 10px;
            padding: 6px 12px;
            font-weight: 700;
            font-size: {small}px;
        }}
        QPushButton#HeaderButton:hover {{
            background: #1b2c40;
            border: 1px solid #6aa8ff;
        }}
        QLineEdit, QPlainTextEdit, QListWidget, QSpinBox, QDoubleSpinBox, QScrollArea {{
            border: 1px solid #2e3d4f;
            border-radius: 10px;
            padding: 6px;
            selection-background-color: #2f87ff;
        }}
        QTabBar::tab {{
            background: #111821;
            border: 1px solid #263241;
            padding: 8px 18px;
            margin-right: 4px;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            font-size: {title}px;
        }}
        QTabBar::tab:selected {{
            background: #192535;
            border-color: #3f74b6;
        }}
        QCheckBox, QRadioButton {{
            spacing: 6px;
            font-size: {base}px;
        }}
        QProgressBar {{
            border: 1px solid #3a4d64;
            border-radius: 10px;
            text-align: center;
            min-height: 22px;
            background: #0a1017;
            font-size: {small}px;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #2f87ff, stop:1 #6fd3ff);
            border-radius: 8px;
        }}
        """)

    def _apply_style(self):
        self._apply_responsive_fonts()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_fonts()

    def log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{ts}] {msg}")

    def set_header_state(self, text: str):
        self.header_state.setText(text)

    def toggle_fullscreen(self):
        if self.isMaximized():
            self.showNormal()
            self.fullscreen_btn.setText("最大化")
            self.log("最大化を解除")
        else:
            self.showMaximized()
            self.fullscreen_btn.setText("元に戻す")
            self.log("最大化")

    def exit_fullscreen(self):
        if self.isMaximized():
            self.showNormal()
            self.fullscreen_btn.setText("最大化")
            self.log("最大化を解除")

    def choose_record_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "保存先を選択", self.rec_dir_edit.text())
        if d:
            self.rec_dir_edit.setText(d)

    def _build_record_path(self) -> str:
        out_dir = self.rec_dir_edit.text().strip() or package_default_save_dir()
        os.makedirs(out_dir, exist_ok=True)
        base = self.rec_name_edit.text().strip() or "record"
        if self.rec_stamp_check.isChecked():
            base += "_" + now_string()
        return os.path.join(out_dir, base + ".bag")

    def refresh_live_topics(self):
        checked = {r.topic_name for r in self.topic_rows if r.is_checked()}
        try:
            topics = sorted(rospy.get_published_topics())
        except Exception as e:
            self.log(f"topic一覧の取得に失敗: {e}")
            topics = []

        self._clear_vlayout_widgets(self.rec_topic_layout)
        self.topic_rows = []
        for name, typ in topics:
            row = TopicRowWidget(name, typ)
            row.set_checked(name in checked)
            self.topic_rows.append(row)
            self.rec_topic_layout.addWidget(row)
        self.rec_topic_layout.addStretch(1)
        self.filter_record_topics()
        self.log(f"現在のtopic一覧を更新: {len(topics)}件")

    def _refresh_live_topics_if_idle(self):
        if self.record_proc is None:
            self.refresh_live_topics()

    def filter_record_topics(self):
        key = self.rec_filter_edit.text().strip().lower()
        hide_system = self.rec_hide_system_check.isChecked()
        hidden_names = {"/rosout", "/rosout_agg", "/tf", "/tf_static"}
        for row in self.topic_rows:
            visible = key in row.topic_name.lower()
            if hide_system and row.topic_name in hidden_names:
                visible = False
            row.setVisible(visible)

    def _set_all_topic_checks(self, rows, checked: bool):
        for r in rows:
            r.set_checked(checked)

    def _set_visible_topic_checks(self, rows, checked: bool):
        for r in rows:
            if r.isVisible():
                r.set_checked(checked)

    def get_checked_live_topics(self) -> List[str]:
        return [r.topic_name for r in self.topic_rows if r.is_checked()]

    def _preset_path_from_name(self, name: str) -> str:
        return os.path.join(package_preset_dir(), sanitize_filename(name) + ".json")

    def refresh_record_presets(self):
        current = self.rec_preset_combo.currentText() if hasattr(self, "rec_preset_combo") else ""
        self.rec_preset_combo.blockSignals(True)
        self.rec_preset_combo.clear()
        names = []
        try:
            for fn in sorted(os.listdir(package_preset_dir())):
                if fn.endswith(".json"):
                    names.append(os.path.splitext(fn)[0])
        except Exception:
            pass
        self.rec_preset_combo.addItems(names)
        idx = self.rec_preset_combo.findText(current)
        if idx >= 0:
            self.rec_preset_combo.setCurrentIndex(idx)
        self.rec_preset_combo.blockSignals(False)

    def save_record_preset(self):
        name = self.rec_preset_name_edit.text().strip() or self.rec_preset_combo.currentText().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "警告", "プリセット名を入力してください。")
            return
        try:
            with open(self._preset_path_from_name(name), "w", encoding="utf-8") as f:
                json.dump({"name": name, "topics": self.get_checked_live_topics(), "updated_at": now_string()}, f, ensure_ascii=False, indent=2)
            self.refresh_record_presets()
            self.log(f"保存プリセットを保存: {name}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "エラー", str(e))

    def load_selected_record_preset(self):
        name = self.rec_preset_combo.currentText().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "警告", "読み込むプリセットを選択してください。")
            return
        try:
            with open(self._preset_path_from_name(name), "r", encoding="utf-8") as f:
                data = json.load(f)
            topics = set(data.get("topics", []))
            for row in self.topic_rows:
                row.set_checked(row.topic_name in topics)
            self.filter_record_topics()
            self.log(f"保存プリセットを読み込み: {name}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "エラー", str(e))

    def delete_selected_record_preset(self):
        name = self.rec_preset_combo.currentText().strip()
        if not name:
            return
        path = self._preset_path_from_name(name)
        if os.path.exists(path):
            os.remove(path)
            self.refresh_record_presets()
            self.log(f"保存プリセットを削除: {name}")

    def start_recording(self):
        if self.record_proc is not None:
            return
        topics = self.get_checked_live_topics()
        if not topics:
            QtWidgets.QMessageBox.warning(self, "警告", "保存するtopicを選択してください。")
            return

        bag_path = self._build_record_path()
        cmd = ["rosbag", "record", "-O", bag_path]
        if self.rec_split_check.isChecked():
            cmd += ["--split", f"--size={self.rec_split_size_spin.value()}"]
        if self.rec_bz2_radio.isChecked():
            cmd += ["--bz2"]
        elif self.rec_lz4_radio.isChecked():
            cmd += ["--lz4"]
        cmd += ["--buffsize", str(self.rec_buff_spin.value() * 1024 * 1024)]
        cmd += ["--chunksize", str(self.rec_chunk_spin.value() * 1024)]
        cmd += topics

        try:
            self.record_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
            self.current_record_target = bag_path
            self.record_started_at = time.time()
            self.last_record_time = self.record_started_at
            self.last_record_size = 0

            self.rec_start_btn.setEnabled(False)
            self.rec_stop_btn.setEnabled(True)
            self.rec_status_label.setText("保存中")
            self.rec_progress.setVisible(True)
            self.rec_target_label.setText(bag_path)
            self.rec_size_label.setText("0 B")
            self.rec_rate_label.setText("0 B/s")
            self.set_header_state("SAVING")
            self.csv_bag_edit.setText(bag_path)
            if not self.csv_out_dir_edit.text().strip():
                self.csv_out_dir_edit.setText(os.path.splitext(bag_path)[0] + "_csv")
            self.log("保存開始: " + quote_join(cmd))
        except Exception as e:
            self.record_proc = None
            self.log(f"保存開始失敗: {e}")
            QtWidgets.QMessageBox.critical(self, "エラー", str(e))

    def stop_recording(self):
        if self.record_proc is None:
            return
        try:
            os.killpg(os.getpgid(self.record_proc.pid), signal.SIGINT)
            self.record_proc.wait(timeout=10)
            self.log("保存停止")
        except Exception as e:
            self.log(f"保存停止で例外: {e}")
            try:
                os.killpg(os.getpgid(self.record_proc.pid), signal.SIGTERM)
            except Exception:
                pass
        finally:
            self.record_proc = None
            self.rec_start_btn.setEnabled(True)
            self.rec_stop_btn.setEnabled(False)
            self.rec_status_label.setText("待機中")
            self.rec_progress.setVisible(False)
            self.rec_rate_label.setText("-")
            self.record_started_at = None
            self.current_record_target = None
            self.last_record_size = 0
            self.last_record_time = 0.0
            self.set_header_state("READY")

    def _record_target_size(self) -> int:
        path = self.current_record_target
        if not path:
            return 0
        total = 0
        try:
            if os.path.exists(path):
                total += os.path.getsize(path)
            parent = os.path.dirname(path) or "."
            stem = os.path.splitext(os.path.basename(path))[0]
            for name in os.listdir(parent):
                if name.startswith(stem + "_") and name.endswith(".bag"):
                    total += os.path.getsize(os.path.join(parent, name))
        except Exception:
            pass
        return total

    def choose_play_bag(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "bagファイルを選択", package_default_save_dir(), "ROS bag (*.bag)")
        if f:
            self.play_bag_edit.setText(f)
            self.load_play_bag_info()

    def load_play_bag_info(self):
        bag_path = self.play_bag_edit.text().strip()
        if not bag_path or not os.path.isfile(bag_path):
            QtWidgets.QMessageBox.warning(self, "警告", "有効なbagファイルを指定してください。")
            return
        self.play_info_label.setText("読み込み中...")
        self.bag_info_worker = BagInfoWorker(bag_path)
        self.bag_info_worker.result_ready.connect(self._on_play_bag_info)
        self.bag_info_worker.error_occurred.connect(self._on_bag_info_error)
        self.bag_info_worker.start()

    def _on_bag_info_error(self, msg: str):
        self.play_info_label.setText("bag情報の取得に失敗")
        self.log("bag情報取得失敗: " + msg)
        QtWidgets.QMessageBox.critical(self, "エラー", msg)

    def _on_play_bag_info(self, info: dict):
        self.current_bag_info = info or {}
        topics = info.get("topics", []) or []
        self._clear_vlayout_widgets(self.play_topic_layout)
        self.play_topic_rows = []
        for t in topics:
            row = TopicRowWidget(t.get("topic", ""), t.get("type", "unknown"))
            row.set_checked(True)
            row.toggled.connect(self._update_play_command_preview)
            self.play_topic_rows.append(row)
            self.play_topic_layout.addWidget(row)
        self.play_topic_layout.addStretch(1)
        self.filter_play_topics()

        try:
            self.play_duration_total = float(info.get("duration", 0.0))
        except Exception:
            self.play_duration_total = 0.0
        messages = info.get("messages", "-")
        size = info.get("size", "-")
        self.play_seek_slider.blockSignals(True)
        self.play_seek_slider.setValue(self._offset_to_slider_value(self.play_start_spin.value()))
        self.play_seek_slider.blockSignals(False)
        self._set_seek_label(self.play_start_spin.value())
        self.play_info_label.setText(f"duration: {self.play_duration_total} s, messages: {messages}, size: {size}, topics: {len(self.play_topic_rows)}")
        self.log(f"bag情報を読み込み: topic {len(self.play_topic_rows)}件")
        self._update_play_command_preview()

        self.csv_bag_edit.setText(self.play_bag_edit.text().strip())
        self.load_csv_topics_from_info(info)

    def filter_play_topics(self):
        key = self.play_filter_edit.text().strip().lower()
        for row in self.play_topic_rows:
            row.setVisible(key in row.topic_name.lower())
        self._update_play_command_preview()

    def get_checked_play_topics(self) -> List[str]:
        return [r.topic_name for r in self.play_topic_rows if r.is_checked()]

    def add_remap_rows_from_selected(self):
        selected = self.get_checked_play_topics()
        existing = {r.src_topic for r in self.remap_rows}
        for topic in selected:
            if topic in existing:
                continue
            row = RemapRowWidget(topic)
            row.remove_btn.clicked.connect(lambda _, rw=row: self.remove_remap_row(rw))
            row.changed.connect(self._update_play_command_preview)
            self.remap_rows.append(row)
            self.remap_layout.insertWidget(max(0, self.remap_layout.count() - 1), row)
        self._update_play_command_preview()

    def remove_remap_row(self, row: RemapRowWidget):
        if row in self.remap_rows:
            self.remap_rows.remove(row)
            row.setParent(None)
            row.deleteLater()
            self._update_play_command_preview()

    def clear_remap_rows(self):
        for row in list(self.remap_rows):
            row.setParent(None)
            row.deleteLater()
        self.remap_rows.clear()
        self._update_play_command_preview()

    def _collect_remap_rules(self) -> List[str]:
        return [rule for rule in (r.get_rule() for r in self.remap_rows) if rule]

    def _build_play_command(self) -> List[str]:
        bag_path = self.play_bag_edit.text().strip()
        cmd = ["rosbag", "play", bag_path]
        selected = self.get_checked_play_topics()
        if selected:
            cmd += ["--topics"] + selected
        if abs(self.play_rate_spin.value() - 1.0) > 1e-9:
            cmd += ["-r", str(self.play_rate_spin.value())]
        if self.play_start_spin.value() > 0.0:
            cmd += ["-s", str(self.play_start_spin.value())]
        if self.play_duration_spin.value() > 0.0:
            cmd += ["-u", str(self.play_duration_spin.value())]
        if self.play_delay_spin.value() > 0.0:
            cmd += ["-d", str(self.play_delay_spin.value())]
        if self.play_clock_check.isChecked():
            cmd += ["--clock"]
        if self.play_keep_alive_check.isChecked():
            cmd += ["--keep-alive"]
        if self.play_pause_check.isChecked():
            cmd += ["--pause"]
        if self.play_loop_check.isChecked():
            cmd += ["--loop"]
        if self.play_quiet_check.isChecked():
            cmd += ["--quiet"]
        if self.play_wait_sub_check.isChecked():
            cmd += ["--wait-for-subscribers"]
        cmd += ["--queue", str(self.play_queue_spin.value())]
        cmd += self._collect_remap_rules()
        return cmd

    def _update_play_command_preview(self):
        bag_path = self.play_bag_edit.text().strip()
        self.play_cmd_preview.setPlainText("" if not bag_path else quote_join(self._build_play_command()))

    def _format_seconds(self, sec: float) -> str:
        sec = max(0.0, float(sec))
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:d}:{m:02d}:{s:04.1f}" if h > 0 else f"{m:d}:{s:04.1f}"

    def _slider_value_to_offset(self, value: int) -> float:
        return 0.0 if self.play_duration_total <= 0 else (float(value) / 1000.0) * self.play_duration_total

    def _offset_to_slider_value(self, offset: float) -> int:
        if self.play_duration_total <= 0:
            return 0
        return max(0, min(1000, int(round(max(0.0, min(offset, self.play_duration_total)) / self.play_duration_total * 1000.0))))

    def _set_seek_label(self, current: float):
        self.play_seek_label.setText(f"{self._format_seconds(current)} / {self._format_seconds(self.play_duration_total)}")

    def on_play_seek_pressed(self):
        self.play_seek_dragging = True

    def on_play_seek_changed(self, value: int):
        if self.play_seek_dragging:
            offset = self._slider_value_to_offset(value)
            self.play_start_spin.setValue(offset)
            self._set_seek_label(offset)

    def on_play_seek_released(self):
        offset = self._slider_value_to_offset(self.play_seek_slider.value())
        self.play_seek_dragging = False
        self.play_start_spin.setValue(offset)
        self._set_seek_label(offset)
        if self.play_proc is not None:
            self.log(f"シーク位置を {offset:.2f} s に変更したため，再生をその位置からやり直します")
            self.stop_playback()
            QtCore.QTimer.singleShot(150, self.start_playback)

    def on_play_start_spin_changed(self, value: float):
        if not self.play_seek_dragging:
            self.play_seek_slider.blockSignals(True)
            self.play_seek_slider.setValue(self._offset_to_slider_value(value))
            self.play_seek_slider.blockSignals(False)
            self._set_seek_label(value)
        self._update_play_command_preview()

    def start_playback(self):
        if self.play_proc is not None:
            return
        bag_path = self.play_bag_edit.text().strip()
        if not bag_path or not os.path.isfile(bag_path):
            QtWidgets.QMessageBox.warning(self, "警告", "再生するbagファイルを指定してください。")
            return
        if self.play_topic_rows and not self.get_checked_play_topics():
            QtWidgets.QMessageBox.warning(self, "警告", "再生するtopicを少なくとも1つ選択してください。")
            return
        cmd = self._build_play_command()
        try:
            if self.play_use_sim_time_check.isChecked():
                subprocess.run(["rosparam", "set", "/use_sim_time", "true"], check=False)
                self.log("/use_sim_time を true に設定")
            self.play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
            self.play_started_at = time.time()
            self.play_start_btn.setEnabled(False)
            self.play_stop_btn.setEnabled(True)
            self.play_status_label.setText("再生中")
            self.set_header_state("PLAYING")
            self.log("再生開始: " + quote_join(cmd))
            self._update_play_command_preview()
        except Exception as e:
            self.play_proc = None
            self.log(f"再生開始失敗: {e}")
            QtWidgets.QMessageBox.critical(self, "エラー", str(e))

    def stop_playback(self):
        if self.play_proc is None:
            return
        try:
            if self.play_proc.poll() is None:
                os.killpg(os.getpgid(self.play_proc.pid), signal.SIGINT)
                self.play_proc.wait(timeout=10)
                self.log("再生停止")
        except Exception as e:
            self.log(f"再生停止で例外: {e}")
            try:
                if self.play_proc is not None and self.play_proc.poll() is None:
                    os.killpg(os.getpgid(self.play_proc.pid), signal.SIGTERM)
            except Exception:
                pass
        finally:
            self.finalize_playback_after_process_end()

    def finalize_playback_after_process_end(self):
        self.play_proc = None
        self.play_started_at = None
        self.play_start_btn.setEnabled(True)
        self.play_stop_btn.setEnabled(False)
        self.play_status_label.setText("待機中")
        if self.play_use_sim_time_check.isChecked():
            subprocess.run(["rosparam", "set", "/use_sim_time", "false"], check=False)
            self.log("/use_sim_time を false に戻しました")
        self.set_header_state("READY")

    def choose_csv_bag(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "bagファイルを選択", package_default_save_dir(), "ROS bag (*.bag)")
        if f:
            self.csv_bag_edit.setText(f)
            self.load_csv_topics_from_bag()

    def choose_csv_out_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "CSV出力先を選択", self.csv_out_dir_edit.text() or package_default_save_dir())
        if d:
            self.csv_out_dir_edit.setText(d)

    def load_csv_topics_from_info(self, info: dict):
        self.csv_topic_list.clear()
        for t in info.get("topics", []) or []:
            item = QtWidgets.QListWidgetItem(t.get("topic", ""))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked)
            self.csv_topic_list.addItem(item)

    def load_csv_topics_from_bag(self):
        bag_path = self.csv_bag_edit.text().strip()
        if not bag_path or not os.path.isfile(bag_path):
            QtWidgets.QMessageBox.warning(self, "警告", "有効なbagファイルを指定してください。")
            return
        self.bag_info_worker = BagInfoWorker(bag_path)
        self.bag_info_worker.result_ready.connect(self.load_csv_topics_from_info)
        self.bag_info_worker.error_occurred.connect(lambda msg: QtWidgets.QMessageBox.critical(self, "エラー", msg))
        self.bag_info_worker.start()
        if not self.csv_out_dir_edit.text().strip():
            self.csv_out_dir_edit.setText(os.path.splitext(bag_path)[0] + "_csv")

    def _set_listwidget_checks(self, lw: QtWidgets.QListWidget, checked: bool):
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for i in range(lw.count()):
            lw.item(i).setCheckState(state)

    def get_csv_selected_topics(self) -> List[str]:
        topics = []
        for i in range(self.csv_topic_list.count()):
            item = self.csv_topic_list.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                topics.append(item.text())
        return topics

    def convert_bag_to_csv(self):
        if self.csv_worker is not None and self.csv_worker.isRunning():
            return
        bag_path = self.csv_bag_edit.text().strip()
        if not bag_path or not os.path.isfile(bag_path):
            QtWidgets.QMessageBox.warning(self, "警告", "有効なbagファイルを指定してください。")
            return
        out_dir = self.csv_out_dir_edit.text().strip() or (os.path.splitext(bag_path)[0] + "_csv")
        self.csv_out_dir_edit.setText(out_dir)
        topics = self.get_csv_selected_topics()
        if not topics:
            QtWidgets.QMessageBox.warning(self, "警告", "CSV変換するtopicを選択してください。")
            return
        self.csv_convert_btn.setEnabled(False)
        self.csv_worker = CsvConvertWorker(bag_path, out_dir, topics)
        self.csv_worker.log_line.connect(self.log)
        self.csv_worker.finished_ok.connect(self._on_csv_ok)
        self.csv_worker.finished_ng.connect(self._on_csv_ng)
        self.csv_worker.start()

    def _on_csv_ok(self, out_dir: str):
        self.csv_convert_btn.setEnabled(True)
        self.log(f"CSV変換完了: {out_dir}")
        QtWidgets.QMessageBox.information(self, "完了", f"CSV変換が完了しました。\n{out_dir}")

    def _on_csv_ng(self, msg: str):
        self.csv_convert_btn.setEnabled(True)
        self.log(f"CSV変換エラー: {msg}")
        QtWidgets.QMessageBox.warning(self, "CSV変換", msg)

    def compress_existing_bag(self):
        if self.compress_worker is not None and self.compress_worker.isRunning():
            return
        bag_path = self.csv_bag_edit.text().strip()
        if not bag_path or not os.path.isfile(bag_path):
            QtWidgets.QMessageBox.warning(self, "警告", "圧縮するbagファイルを指定してください。")
            return
        mode = self.bag_compress_mode_combo.currentText().strip()
        self.bag_compress_btn.setEnabled(False)
        self.compress_worker = BagCompressWorker(bag_path, mode)
        self.compress_worker.log_line.connect(self.log)
        self.compress_worker.finished_ok.connect(self._on_compress_ok)
        self.compress_worker.finished_ng.connect(self._on_compress_ng)
        self.compress_worker.start()

    def _on_compress_ok(self, bag_path: str):
        self.bag_compress_btn.setEnabled(True)
        self.log(f"bag圧縮完了: {bag_path}")
        QtWidgets.QMessageBox.information(self, "完了", f"bag圧縮が完了しました。\n{bag_path}")

    def _on_compress_ng(self, msg: str):
        self.bag_compress_btn.setEnabled(True)
        self.log(f"bag圧縮エラー: {msg}")
        QtWidgets.QMessageBox.warning(self, "bag圧縮", msg)

    def _update_runtime_status(self):
        if self.record_proc is not None:
            if self.record_proc.poll() is not None:
                self.log("保存プロセスが終了しました")
                self.stop_recording()
            else:
                elapsed = max(0.0, time.time() - (self.record_started_at or time.time()))
                self.rec_elapsed_label.setText(f"{elapsed:.1f} s")
                size_now = self._record_target_size()
                self.rec_size_label.setText(human_bytes(size_now))
                dt = max(1e-6, time.time() - self.last_record_time)
                rate = (size_now - self.last_record_size) / dt
                self.rec_rate_label.setText(human_bps(rate))
                self.last_record_size = size_now
                self.last_record_time = time.time()
        else:
            self.rec_elapsed_label.setText("0.0 s")

        if self.play_proc is not None:
            if self.play_proc.poll() is not None:
                self.log("再生プロセスが終了しました")
                self.finalize_playback_after_process_end()
            else:
                elapsed = max(0.0, time.time() - (self.play_started_at or time.time()))
                self.play_elapsed_label.setText(f"{elapsed:.1f} s")
                if not self.play_seek_dragging:
                    current = self.play_start_spin.value() + elapsed * self.play_rate_spin.value()
                    self.play_seek_slider.blockSignals(True)
                    self.play_seek_slider.setValue(self._offset_to_slider_value(current))
                    self.play_seek_slider.blockSignals(False)
                    self._set_seek_label(current)
        else:
            self.play_elapsed_label.setText("0.0 s")
            if not self.play_seek_dragging:
                self._set_seek_label(self.play_start_spin.value())

    def _clear_vlayout_widgets(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            child_layout = item.layout()
            if w is not None:
                w.deleteLater()
            elif child_layout is not None:
                self._clear_layout_recursive(child_layout)

    def _clear_layout_recursive(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            child_layout = item.layout()
            if w is not None:
                w.deleteLater()
            elif child_layout is not None:
                self._clear_layout_recursive(child_layout)

    def closeEvent(self, event):
        if self.record_proc is not None:
            self.stop_recording()
        if self.play_proc is not None:
            self.stop_playback()
        event.accept()


def main():
    rospy.init_node("rosbag_gui", anonymous=True, disable_signals=True)
    app = QtWidgets.QApplication(sys.argv)
    w = RosbagGuiWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
