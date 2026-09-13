from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMessageBox

from app.main_window import button
from app.main_window_v209 import _is_connection_error
from app.main_window_v214 import DelayedLoginBrowserWorker, MainWindow as V214MainWindow
from app.rpa.publisher import DouDianSimilarPublisher


class _UserCancelledTask(RuntimeError):
    pass


class CancellableBrowserWorker(DelayedLoginBrowserWorker):
    """V2.1.5: allow the current publish task to be cancelled without closing the store browser.

    Cancellation is cooperative and is checked at every RPA step and during the added
    2-second step delay. Remaining queued tasks are returned to pending so the batch
    does not automatically continue after an emergency task stop.
    """

    current_task_changed = Signal(bool, int, str)
    task_cancelled = Signal(int, str, bool, str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cancel_current = threading.Event()
        self.current_task_id: int | None = None
        self.current_task_code = ""

    def request_cancel_current(self) -> bool:
        if self.current_task_id is None:
            return False

        self._cancel_current.set()

        # If the task is paused, cancellation must wake it immediately rather than
        # waiting for the user to press "continue".
        try:
            self._pause_requested.clear()
            self._resume_gate.set()
        except Exception:
            pass

        # Do not automatically start any queued task after the current task is stopped.
        # _queue_paused returns those tasks to "待执行" through the existing UI handler.
        try:
            self._pause_remaining("用户已结束当前任务，后续排队任务已恢复为待执行")
        except Exception:
            pass

        self.info.emit(
            f"已收到结束任务指令：{self.current_task_code}。正在终止当前 RPA 任务…"
        )
        return True

    def _raise_if_cancelled(self):
        if self._cancel_current.is_set():
            raise _UserCancelledTask("用户主动结束任务")

    def _execute_publish(self, task, template, safe_mode):
        tid = int(task["id"])
        code = str(task["task_code"])

        self.current_task_id = tid
        self.current_task_code = code
        self._cancel_current.clear()
        self.current_task_changed.emit(True, tid, code)

        pub = None
        try:
            self._wait_if_paused(code)
            self._raise_if_cancelled()
            if self._stop:
                return

            if not self._is_live():
                if not self._reconnect(attempts=2):
                    self.task_error.emit(
                        tid, code, "浏览器连接中断，自动重连失败；批量队列已暂停。"
                    )
                    self._pause_remaining("浏览器连接中断，自动重连失败")
                    return

            retried = False
            while True:
                pub = None
                try:
                    self._raise_if_cancelled()

                    def step_callback(step, prog, tid=tid, code=code):
                        self._raise_if_cancelled()
                        self.task_step.emit(tid, code, step, prog)
                        self._wait_if_paused(code)
                        self._raise_if_cancelled()
                        if self._stop:
                            raise RuntimeError("任务执行已停止")

                        # Keep V2.1.4.3 behavior: every RPA step waits an extra 2 seconds.
                        delay_deadline = time.monotonic() + 2.0
                        while time.monotonic() < delay_deadline:
                            self._raise_if_cancelled()
                            self._wait_if_paused(code)
                            self._raise_if_cancelled()
                            if self._stop:
                                raise RuntimeError("任务执行已停止")
                            time.sleep(min(0.2, max(0.0, delay_deadline - time.monotonic())))

                    pub = DouDianSimilarPublisher(
                        self.page,
                        self.selector_file,
                        self.screenshot_dir,
                        step_cb=step_callback,
                    )
                    status, text = pub.run(task, template, safe_mode=safe_mode)
                    self.page = pub.page

                    # A stop click that happens at the very end still wins before the
                    # task is reported as completed.
                    self._raise_if_cancelled()
                    self.task_done.emit(tid, code, status, text)

                    if status == "成功" and not safe_mode:
                        # Keep V2.1.4: 10~15s random interval before the next item.
                        self._schedule_next_publish_delay(code)

                        if not pub.ready_for_next:
                            self.info.emit(
                                f"{code} 已发布成功，但未恢复商品管理页，正在重建浏览器环境。"
                            )
                            if not self._reconnect(attempts=2):
                                self._pause_remaining(
                                    "当前商品已发布成功，但无法恢复商品管理/浏览器连接；后续任务已暂停"
                                )
                    return

                except _UserCancelledTask:
                    risky = bool(pub is not None and getattr(pub, "publish_clicked", False))
                    message = (
                        "用户已结束任务；发布按钮可能已经点击，请先到抖店确认该商品是否已发布，"
                        "确认后再决定是否重试。"
                        if risky
                        else "用户已结束当前任务。"
                    )
                    self.task_cancelled.emit(tid, code, risky, message)
                    return

                except Exception as exc:
                    if pub is not None:
                        try:
                            self.page = pub.page
                        except Exception:
                            pass

                    if self._cancel_current.is_set():
                        risky = bool(
                            pub is not None and getattr(pub, "publish_clicked", False)
                        )
                        message = (
                            "用户已结束任务；发布按钮可能已经点击，请先到抖店确认该商品是否已发布，"
                            "确认后再决定是否重试。"
                            if risky
                            else "用户已结束当前任务。"
                        )
                        self.task_cancelled.emit(tid, code, risky, message)
                        return

                    if self._stop:
                        return

                    if _is_connection_error(exc):
                        if pub is not None and getattr(pub, "publish_clicked", False):
                            self.task_error.emit(
                                tid,
                                code,
                                "提交发布后浏览器连接断开。为避免重复发布，"
                                "当前任务不自动重试；请人工确认是否已发布，批量队列已暂停。",
                            )
                            self._pause_remaining(
                                "提交后连接中断，为避免重复发布已暂停批量队列"
                            )
                            return

                        if not retried:
                            self.info.emit(
                                f"{code} 执行中检测到浏览器连接断开，"
                                "准备重连并重试当前任务 1 次。"
                            )
                            if self._reconnect(attempts=2):
                                retried = True
                                continue

                        self.task_error.emit(
                            tid,
                            code,
                            "浏览器连接中断且自动重连失败；"
                            "当前任务失败，后续批量任务已暂停。",
                        )
                        self._pause_remaining("浏览器连接中断，自动重连失败")
                        return

                    self.task_error.emit(tid, code, str(exc))
                    return
        finally:
            self._cancel_current.clear()
            self.current_task_id = None
            self.current_task_code = ""
            self.current_task_changed.emit(False, tid, code)


class MainWindow(V214MainWindow):
    """V2.1.5: delete selected task + stop current task, preserving all prior behavior."""

    # ---------- UI ----------
    def _dashboard_page(self):
        page = super()._dashboard_page()

        self.end_task_button = button("结束任务", "danger")
        self.end_task_button.setEnabled(False)
        self.end_task_button.setToolTip(
            "立即停止当前正在执行的任务；浏览器保持打开，后续排队任务恢复为待执行。"
        )
        self.end_task_button.clicked.connect(self.end_current_task)

        # V2.1.3 already creates end_run_button in the "当前运行" card.
        # Insert "结束任务" immediately before it.
        try:
            run_card = self.end_run_button.parentWidget()
            run_layout = run_card.layout()
            index = run_layout.indexOf(self.end_run_button)
            run_layout.insertWidget(max(0, index), self.end_task_button)
        except Exception:
            # Fallback: if the inherited card layout changes later, keep the button visible.
            try:
                page.layout().addWidget(self.end_task_button)
            except Exception:
                pass

        return page

    def _tasks_page(self):
        page = super()._tasks_page()

        self.delete_task_button = button("删除指定任务", "danger")
        self.delete_task_button.clicked.connect(self.delete_selected_tasks)

        # In V2.1.1 the first layout of the task page is the action bar.
        try:
            action_layout = page.layout().itemAt(0).layout()
            # Place it after "重置失败任务" and before the stretch/search area.
            action_layout.insertWidget(7, self.delete_task_button)
        except Exception:
            try:
                page.layout().insertWidget(0, self.delete_task_button)
            except Exception:
                pass

        return page

    # ---------- browser ----------
    def open_store_browser(self):
        store = self._selected_store()
        if not store:
            self.db.add_store(
                "默认店铺",
                str(self.root / "data" / "profiles" / "default"),
            )
            store = self.db.stores()[0]
            self.refresh_all()

        if self.browser_worker and self.browser_worker.isRunning():
            if getattr(self.browser_worker, "connected", False):
                active = (
                    self.db.store(self.active_store_id)
                    if self.active_store_id else None
                )
                active_name = active["name"] if active else "当前店铺"
                QMessageBox.information(
                    self,
                    "浏览器正在运行",
                    f"{active_name} 的浏览器仍在运行。\n\n"
                    "需要切换店铺时，请先在工作台点击“结束运行”，"
                    "再选择其他店铺并打开。",
                )
                return

            self.browser_worker.request_stop()
            self.browser_worker.wait(2500)
            if self.browser_worker.isRunning():
                QMessageBox.warning(
                    self,
                    "正在结束旧浏览器",
                    "请稍等 1~2 秒后再次点击打开店铺。",
                )
                return

        self.active_store_id = int(store["id"])
        self.run_status.setText("正在启动浏览器")
        self.run_desc.setText(f"店铺：{store['name']}")
        self.run_progress.setValue(3)

        worker = CancellableBrowserWorker(
            Path(store["profile_dir"]),
            self.root / "config" / "selectors.json",
            self.root / "screenshots",
            self,
        )
        self.browser_worker = worker
        worker.ready.connect(
            lambda msg, sid=int(store["id"]): self._browser_ready(sid, msg)
        )
        worker.task_step.connect(self._task_step)
        worker.task_done.connect(self._task_done)
        worker.task_error.connect(self._task_error)
        worker.info.connect(self._worker_info)
        worker.connection_state.connect(self._connection_state)
        worker.queue_paused.connect(self._queue_paused)
        worker.pause_state.connect(self._pause_state_changed)
        worker.browser_closed.connect(self._browser_closed)
        worker.current_task_changed.connect(self._current_task_changed)
        worker.task_cancelled.connect(self._task_cancelled)
        worker.finished.connect(lambda w=worker: self._worker_finished_cleanup(w))
        worker.start()
        self.db.add_log("INFO", f"启动店铺浏览器：{store['name']}")

    # ---------- stop current task ----------
    def _current_task_changed(self, running: bool, task_id: int, code: str):
        if hasattr(self, "end_task_button"):
            self.end_task_button.setEnabled(bool(running))

    def end_current_task(self):
        worker = self.browser_worker
        if (
            not worker
            or not worker.isRunning()
            or not isinstance(worker, CancellableBrowserWorker)
            or worker.current_task_id is None
        ):
            QMessageBox.information(
                self,
                "没有正在执行的任务",
                "当前没有需要结束的执行任务。",
            )
            return

        code = worker.current_task_code or "当前任务"
        if not worker.request_cancel_current():
            return

        # No confirmation dialog: the click itself is the emergency stop command.
        self.end_task_button.setEnabled(False)
        self.run_status.setText("正在结束任务")
        self.run_desc.setText(
            f"{code} · 已发送终止指令；浏览器保持打开，后续任务不会自动继续。"
        )

    def _task_cancelled(self, tid: int, code: str, risky: bool, message: str):
        error = message
        self.db.update_task(
            int(tid),
            status="失败",
            progress=0,
            step="用户结束任务",
            error=error,
        )
        self.db.add_log("WARNING", error, code)

        self.run_status.setText("任务已结束")
        self.run_desc.setText(f"{code} · {message}")
        self.run_progress.setValue(0)
        if hasattr(self, "end_task_button"):
            self.end_task_button.setEnabled(False)

        self.refresh_all()

        if risky:
            QMessageBox.warning(
                self,
                "任务已结束，请检查是否已发布",
                f"{code}\n\n{message}",
            )

    # ---------- delete specified task ----------
    def _active_task_table(self):
        if hasattr(self, "task_tabs") and self.task_tabs.currentIndex() == 1:
            return self.failed_table
        return self.pending_table

    def delete_selected_tasks(self):
        table = self._active_task_table()
        if table is None:
            return

        rows = sorted({idx.row() for idx in table.selectionModel().selectedRows()})
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]

        if not rows:
            QMessageBox.information(
                self,
                "未选择任务",
                "请先选择需要删除的指定任务。",
            )
            return

        tasks = []
        missing = []
        for row in rows:
            item = table.item(row, 0)
            if not item:
                continue
            task = self.db.task(int(item.text()))
            if task:
                tasks.append(task)
            else:
                missing.append(item.text())

        if not tasks:
            self.refresh_all()
            return

        # Do not delete data for a task that the worker still owns.
        blocked = [
            t for t in tasks
            if str(t["status"]).strip() in ("执行中", "排队中")
        ]
        deletable = [
            t for t in tasks
            if str(t["status"]).strip() not in ("执行中", "排队中")
        ]

        if blocked:
            codes = "、".join(str(t["task_code"]) for t in blocked[:8])
            QMessageBox.warning(
                self,
                "部分任务不能删除",
                f"以下任务正在执行或已经进入执行队列，暂不能直接删除：\n{codes}\n\n"
                "如果是当前正在执行的任务，请先在工作台点击“结束任务”；"
                "排队任务会随之恢复为待执行，然后即可删除。",
            )

        if not deletable:
            return

        codes = [str(t["task_code"]) for t in deletable]
        preview = "\n".join(codes[:12])
        if len(codes) > 12:
            preview += f"\n……另 {len(codes) - 12} 条"

        answer = QMessageBox.question(
            self,
            "删除指定任务",
            f"确认删除以下 {len(codes)} 条任务？\n\n{preview}\n\n"
            "删除后将从裂变任务列表中移除，无法撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        for task in deletable:
            self.db.delete_task(int(task["id"]))
            self.db.add_log(
                "INFO",
                "用户删除指定裂变任务",
                str(task["task_code"]),
            )

        self.refresh_all()
