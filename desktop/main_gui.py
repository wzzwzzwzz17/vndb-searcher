# -*- coding: utf-8 -*-
"""本地 GUI：新一轮 / 刷新 / 优化上轮 / 标签中文 / 双击详情 / 收藏。"""

from __future__ import annotations

import sys
import traceback
from typing import Any

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
import favorites
from backend_server import (
    BackendError,
    fetch_vn_detail,
    optimize_round,
    recommend_new_round,
    refresh_round,
)
from session_store import SESSION


class Worker(QThread):
    finished_ok = pyqtSignal(dict)
    finished_err = pyqtSignal(str)

    def __init__(self, action: str, **kwargs: Any) -> None:
        super().__init__()
        self.action = action
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.action == "new":
                result = recommend_new_round(**self.kwargs)
            elif self.action == "refresh":
                result = refresh_round()
            elif self.action == "optimize":
                result = optimize_round(**self.kwargs)
            elif self.action == "detail":
                detail = fetch_vn_detail(self.kwargs["vn_id"])
                result = {"_detail": True, "detail": detail}
            else:
                raise BackendError(f"未知动作: {self.action}")
            self.finished_ok.emit(result)
        except BackendError as e:
            self.finished_err.emit(str(e))
        except Exception:
            self.finished_err.emit(traceback.format_exc())


class FavoritesDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("收藏夹")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"存档文件：{config.FAVORITES_FILE}"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)
        btn_row = QHBoxLayout()
        remove_btn = QPushButton("删除勾选")
        remove_btn.clicked.connect(self._remove_checked)
        btn_row.addWidget(remove_btn)
        refresh_btn = QPushButton("刷新列表")
        refresh_btn.clicked.connect(self.reload)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(close)
        self.reload()

    def reload(self) -> None:
        self.list_widget.clear()
        for it in favorites.list_items():
            title = it.get("title") or it.get("id") or "?"
            rating = it.get("rating")
            rating_s = f"{rating:.2f}" if isinstance(rating, (int, float)) else "N/A"
            text = f"{title}  |  评分 {rating_s}  |  {it.get('released') or '?'}  |  {it.get('url') or ''}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, it)
            self.list_widget.addItem(item)

    def _remove_checked(self) -> None:
        ids = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                data = item.data(Qt.UserRole) or {}
                if data.get("id"):
                    ids.append(data["id"])
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选要删除的条目")
            return
        n = favorites.remove_ids(ids)
        QMessageBox.information(self, "完成", f"已删除 {n} 条")
        self.reload()


class OptimizeDialog(QDialog):
    def __init__(self, previous_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("优化上轮分析")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "可修改或补充上轮自然语言。AI 会在上轮标签基础上增加/减少标签后重新检索。"
            )
        )
        self.editor = QTextEdit()
        self.editor.setPlainText(previous_text)
        layout.addWidget(self.editor, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.editor.toPlainText().strip()


class DetailDialog(QDialog):
    def __init__(self, detail: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = detail.get("title") or detail.get("id") or "作品详情"
        self.setWindowTitle(f"作品详情 · {title}")
        self.resize(760, 560)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.addRow("标题", QLabel(detail.get("title") or "-"))
        form.addRow("原名", QLabel(detail.get("original") or "-"))
        rating = detail.get("rating")
        form.addRow(
            "评分",
            QLabel(f"{rating:.2f}" if isinstance(rating, (int, float)) else "N/A"),
        )
        form.addRow("发售", QLabel(str(detail.get("released") or "-")))
        form.addRow("链接", QLabel(detail.get("url") or "-"))

        devs = detail.get("developers") or []
        if devs:
            parts = []
            for d in devs:
                name = d.get("name") or ""
                orig = d.get("original") or ""
                parts.append(f"{name}" + (f"（{orig}）" if orig else ""))
            pub = "；".join(parts)
        else:
            pub = "（暂无开发商/出版社信息）"
        form.addRow("出版社/开发商", QLabel(pub))
        top_zh = detail.get("top_tags_zh") or ""
        if top_zh:
            form.addRow("其它高权重标签", QLabel(top_zh))
        layout.addLayout(form)

        layout.addWidget(QLabel("剧情简介（中文）："))
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(detail.get("description") or "（无简介）")
        layout.addWidget(box, stretch=1)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(close)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Galgame 推荐系统 · VNDB")
        self.resize(1100, 900)
        self._worker: Worker | None = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(8)

        tip = QLabel(
            f'输入必须以「{config.INPUT_PREFIX}」开头。'
            "本轮分析与结果会保留，直到你点击「开始新一轮搜索」。"
            f" 收藏存档：{config.FAVORITES_FILE}"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addWidget(QLabel("偏好描述："))
        self.input_box = QTextEdit()
        self.input_box.setPlaceholderText(
            f"{config.INPUT_PREFIX}校园纯爱、日常治愈、无刀、画风清新、高分口碑作品"
        )
        self.input_box.setPlainText(
            f"{config.INPUT_PREFIX}校园纯爱、日常治愈、无刀、画风清新、高分口碑作品"
        )
        self.input_box.setMinimumHeight(110)
        layout.addWidget(self.input_box)

        layout.addWidget(QLabel("搜索模式："))
        mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.radio_quality = QRadioButton("总质量优先")
        self.radio_quality.setChecked(True)
        self.mode_group.addButton(self.radio_quality, 0)
        mode_row.addWidget(self.radio_quality)
        self.radio_content = QRadioButton("内容权重优先")
        self.mode_group.addButton(self.radio_content, 1)
        mode_row.addWidget(self.radio_content)
        self.radio_hardcore = QRadioButton("硬核内容要求")
        self.mode_group.addButton(self.radio_hardcore, 2)
        mode_row.addWidget(self.radio_hardcore)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self.mode_hint = QLabel("")
        self.mode_hint.setWordWrap(True)
        layout.addWidget(self.mode_hint)
        self.radio_quality.toggled.connect(self._update_mode_hint)
        self.radio_content.toggled.connect(self._update_mode_hint)
        self.radio_hardcore.toggled.connect(self._update_mode_hint)
        self._update_mode_hint()

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel("发售年份最低限度："))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(1980, 2035)
        self.year_spin.setValue(config.DEFAULT_MIN_YEAR)
        year_row.addWidget(self.year_spin)
        year_row.addWidget(QLabel("年及以后"))
        year_row.addStretch(1)
        self.session_label = QLabel("会话：尚未开始（请点「开始新一轮搜索」）")
        year_row.addWidget(self.session_label)
        layout.addLayout(year_row)

        self.single_core_cb = QCheckBox("仅按一个核心标签搜索")
        self.single_core_cb.setToolTip(
            "启用后：AI 只选 1 个核心标签参与搜索（仍保留评分/年份等条件）。"
        )
        layout.addWidget(self.single_core_cb)

        # 主操作按钮
        btn_row = QHBoxLayout()
        self.new_btn = QPushButton("开始新一轮搜索")
        self.new_btn.setMinimumHeight(36)
        self.new_btn.clicked.connect(self.on_new_round)
        btn_row.addWidget(self.new_btn)

        self.refresh_btn = QPushButton("刷新结果")
        self.refresh_btn.setToolTip("基于上轮条件再取一批，尽量避开已看过的游戏")
        self.refresh_btn.clicked.connect(self.on_refresh)
        btn_row.addWidget(self.refresh_btn)

        self.optimize_btn = QPushButton("优化上轮分析")
        self.optimize_btn.setToolTip("修改/补充上轮自然语言，AI 增删标签后重搜")
        self.optimize_btn.clicked.connect(self.on_optimize)
        btn_row.addWidget(self.optimize_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 收藏相关
        fav_row = QHBoxLayout()
        self.fav_add_btn = QPushButton("加入收藏夹")
        self.fav_add_btn.clicked.connect(self.on_add_favorites)
        fav_row.addWidget(self.fav_add_btn)
        self.fav_view_btn = QPushButton("打开收藏夹")
        self.fav_view_btn.clicked.connect(self.on_view_favorites)
        fav_row.addWidget(self.fav_view_btn)
        self.clear_view_btn = QPushButton("清空显示")
        self.clear_view_btn.setToolTip("仅清空界面显示，不会丢弃会话；新一轮才会重置分析")
        self.clear_view_btn.clicked.connect(self.on_clear_view)
        fav_row.addWidget(self.clear_view_btn)
        fav_row.addStretch(1)
        layout.addLayout(fav_row)

        layout.addWidget(QLabel("本轮分析摘要（含中文标签）："))
        self.summary_box = QTextEdit()
        self.summary_box.setReadOnly(True)
        self.summary_box.setMinimumHeight(160)
        mono = QFont("Microsoft YaHei UI")
        mono.setPointSize(10)
        self.summary_box.setFont(mono)
        layout.addWidget(self.summary_box)

        layout.addWidget(QLabel("推荐列表（勾选收藏；双击查看剧情与出版社）："))
        self.result_list = QListWidget()
        self.result_list.setFont(mono)
        self.result_list.setMinimumHeight(280)
        self.result_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.result_list, stretch=1)

        key_ok = bool(config.DEEPSEEK_API_KEY) and not config.DEEPSEEK_API_KEY.startswith(
            "在此填入"
        )
        status = "DeepSeek Key: 已配置" if key_ok else "DeepSeek Key: 未配置（请编辑 config.py）"
        self.status_label = QLabel(status)
        layout.addWidget(self.status_label)
        self._sync_session_label()

    def _current_mode(self) -> str:
        if self.radio_hardcore.isChecked():
            return config.SEARCH_MODE_HARDCORE
        if self.radio_content.isChecked():
            return config.SEARCH_MODE_CONTENT
        return config.SEARCH_MODE_QUALITY

    def _update_mode_hint(self, _checked: bool = False) -> None:
        if self.radio_hardcore.isChecked():
            self.mode_hint.setText(
                f"硬核内容要求：核心标签 ≥ {config.HARDCORE_CORE_TAG_LEVEL}"
                f"（优先 {config.HARDCORE_CORE_TAG_LEVEL_PREF}），"
                f"评分 ≥ {config.HARDCORE_MIN_RATING/10:.1f}；允许无结果。"
            )
        elif self.radio_content.isChecked():
            self.mode_hint.setText(
                f"内容权重优先：核心 ≥ {config.CONTENT_CORE_TAG_LEVEL}，次要 2.0/1.6；"
                f"评分约 ≥ {config.CONTENT_MODE_MIN_RATING/10:.1f}。"
            )
        else:
            self.mode_hint.setText(
                f"总质量优先：评分优先（默认 ≥ {config.QUALITY_MODE_MIN_RATING/10:.1f}），标签强度可较低。"
            )

    def _sync_session_label(self) -> None:
        if SESSION.has_round():
            self.session_label.setText(
                f"会话：进行中｜已展示 {len(SESSION.seen_ids)} 部｜"
                f"{config.SEARCH_MODE_LABELS.get(SESSION.search_mode, '')}"
            )
        else:
            self.session_label.setText("会话：尚未开始（请点「开始新一轮搜索」）")

    def _busy(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        for b in (
            self.new_btn,
            self.refresh_btn,
            self.optimize_btn,
            self.fav_add_btn,
        ):
            b.setEnabled(not busy)
        if msg:
            self.summary_box.setPlainText(msg)

    def _start_worker(self, action: str, **kwargs: Any) -> None:
        if self._busy():
            QMessageBox.information(self, "提示", "正在处理中，请稍候")
            return
        self._worker = Worker(action, **kwargs)
        self._worker.finished_ok.connect(self.on_ok)
        self._worker.finished_err.connect(self.on_err)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._set_busy(True)
        self._worker.start()

    def on_new_round(self) -> None:
        text = self.input_box.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先输入描述")
            return
        if SESSION.has_round():
            ret = QMessageBox.question(
                self,
                "开始新一轮？",
                "将清空当前会话的分析与已展示记录，确定开始新一轮搜索吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
        mode = self._current_mode()
        year = int(self.year_spin.value())
        single = self.single_core_cb.isChecked()
        label = config.SEARCH_MODE_LABELS[mode]
        self._start_worker(
            "new",
            raw_user_input=text,
            search_mode=mode,
            min_year=year,
            single_core_only=single,
        )
        core_s = "，单核心标签" if single else ""
        self.summary_box.setPlainText(
            f"正在开始新一轮「{label}」搜索（年份 ≥ {year}{core_s}），请稍候…"
        )
        self.result_list.clear()

    def on_refresh(self) -> None:
        if not SESSION.has_round():
            QMessageBox.information(self, "提示", "请先完成一轮搜索")
            return
        self.summary_box.setPlainText("正在刷新结果（尽量避开已看过的游戏）…")
        self._start_worker("refresh")

    def on_optimize(self) -> None:
        if not SESSION.has_round():
            QMessageBox.information(self, "提示", "请先完成一轮搜索")
            return
        dlg = OptimizeDialog(SESSION.raw_input or self.input_box.toPlainText(), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        new_text = dlg.text()
        if not new_text:
            QMessageBox.warning(self, "提示", "描述不能为空")
            return
        self.input_box.setPlainText(new_text)
        self.summary_box.setPlainText("正在根据你的修改优化上轮标签并重搜…")
        self._start_worker(
            "optimize",
            new_raw_input=new_text,
            single_core_only=self.single_core_cb.isChecked(),
        )

    def on_clear_view(self) -> None:
        self.summary_box.clear()
        self.result_list.clear()
        # 不清 SESSION

    def on_ok(self, payload: dict) -> None:
        if payload.get("_detail"):
            key_ok = bool(config.DEEPSEEK_API_KEY) and not config.DEEPSEEK_API_KEY.startswith(
                "在此填入"
            )
            self.status_label.setText(
                "DeepSeek Key: 已配置" if key_ok else "DeepSeek Key: 未配置（请编辑 config.py）"
            )
            DetailDialog(payload.get("detail") or {}, self).exec_()
            return

        text = payload.get("text") or ""
        items = payload.get("items") or []
        tag_rows = payload.get("tag_zh_rows") or []

        # 摘要顶部再强调中文标签
        zh_line = ""
        if tag_rows:
            from tag_i18n import format_tags_zh_line

            zh_line = format_tags_zh_line(tag_rows)
        header = ""
        if zh_line:
            header = f"【本轮标签·中文】{zh_line}\n\n"
        self.summary_box.setPlainText(header + text)

        self.result_list.clear()
        mono_tip = "双击查看剧情与出版社"
        for it in items:
            title = it.get("title") or "(无标题)"
            original = it.get("original") or ""
            rating = it.get("rating")
            rating_s = f"{rating:.2f}" if isinstance(rating, (int, float)) else "N/A"
            released = it.get("released") or "?"
            url = it.get("url") or ""
            top_zh = it.get("top_tags_zh") or ""
            line = title
            if original:
                line += f" / {original}"
            line += f"  |  评分 {rating_s}  |  {released}"
            if top_zh:
                line += f"  |  标签: {top_zh}"
            item = QListWidgetItem(line)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, it)
            tip = (it.get("description") or "")[:240]
            item.setToolTip(f"{mono_tip}\n{url}\n其它高权重标签: {top_zh}\n{tip}")
            self.result_list.addItem(item)

        self._sync_session_label()

    def on_err(self, msg: str) -> None:
        self.summary_box.setPlainText(f"错误：\n{msg}")
        self._sync_session_label()

    def on_item_double_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        vn_id = data.get("id")
        if not vn_id:
            return
        self.status_label.setText(f"正在加载详情 {vn_id} …")
        self._start_worker("detail", vn_id=vn_id)

    def on_add_favorites(self) -> None:
        selected: list[dict[str, Any]] = []
        for i in range(self.result_list.count()):
            item = self.result_list.item(i)
            if item.checkState() == Qt.Checked:
                data = item.data(Qt.UserRole)
                if isinstance(data, dict):
                    selected.append(data)
        if not selected:
            QMessageBox.information(self, "提示", "请先勾选要收藏的作品")
            return
        n = favorites.add_items(selected)
        QMessageBox.information(
            self, "收藏夹", f"新增 {n} 条。\n存档：{config.FAVORITES_FILE}"
        )

    def on_view_favorites(self) -> None:
        FavoritesDialog(self).exec_()


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
