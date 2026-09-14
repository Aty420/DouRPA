from __future__ import annotations

from app.main_window_v218 import MainWindow as V218MainWindow


class MainWindow(V218MainWindow):
    """V2.1.9: dashboard Start Publish button becomes End Publish while a batch is active.

    Business behavior remains the existing V2.1.5 cancellation model:
    - stop the current task cooperatively;
    - return remaining queued tasks to pending;
    - keep the store browser open.
    """

    def __init__(self, root):
        self._dashboard_publish_stop_mode = False
        super().__init__(root)
        self._set_publish_button_running(False)

        # Rebind the reference button from direct run_pending_tasks to a state-aware handler.
        try:
            self.dashboard_publish_button.clicked.disconnect()
        except Exception:
            pass
        self.dashboard_publish_button.clicked.connect(self._dashboard_publish_action)

    def _dashboard_publish_action(self):
        if self._dashboard_publish_stop_mode:
            self._end_publish_from_dashboard()
            return

        # Keep the original batch-publish behavior from V2.1.8.
        self.run_pending_tasks()
        self._sync_publish_button_state()

    def _end_publish_from_dashboard(self):
        """End the current publish task and stop the rest of this batch.

        This intentionally does NOT close the browser. It is the existing
        "结束任务" semantics presented in the reference-style dashboard button.
        """
        worker = getattr(self, "browser_worker", None)

        # Current task is actively owned by the cancellable worker.
        if (
            worker
            and worker.isRunning()
            and hasattr(worker, "current_task_id")
            and worker.current_task_id is not None
        ):
            # Existing V2.1.5 cancellation path:
            # current task -> failed/user ended; remaining queued -> pending.
            self.end_current_task()
            self._set_publish_button_running(False)
            return

        # Edge case: user clicks during the 10~N second inter-task interval.
        # There may be queued work but no current_task_id for a brief period.
        # Return queued tasks to pending immediately and prevent the next item
        # from starting, without closing the browser.
        if worker and worker.isRunning():
            try:
                worker._pause_remaining(
                    "用户点击结束发布，后续排队任务已恢复为待执行"
                )
            except Exception:
                pass

        try:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status='待执行',
                        progress=0,
                        current_step='等待',
                        last_error='',
                        updated_at=CURRENT_TIMESTAMP
                    WHERE status='排队中'
                    """
                )
        except Exception:
            pass

        self.run_status.setText("发布已结束")
        self.run_desc.setText("本轮批量发布已停止，浏览器保持打开。")
        self.run_progress.setValue(0)
        self.db.add_log("INFO", "用户点击工作台“结束发布”，本轮批量发布已停止")
        self.refresh_all()
        self._set_publish_button_running(False)

    def _has_active_publish_work(self) -> bool:
        worker = getattr(self, "browser_worker", None)

        if (
            worker
            and worker.isRunning()
            and getattr(worker, "current_task_id", None) is not None
        ):
            return True

        try:
            with self.db.connect() as conn:
                count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM tasks
                    WHERE status IN ('排队中','执行中')
                    """
                ).fetchone()[0]
            return bool(count)
        except Exception:
            return False

    def _set_publish_button_running(self, running: bool):
        if not hasattr(self, "dashboard_publish_button"):
            return

        self._dashboard_publish_stop_mode = bool(running)

        if running:
            self.dashboard_publish_button.setText("■  结束发布")
            self.dashboard_publish_button.setObjectName("ReferenceStopPublishButton")
            self.dashboard_publish_button.setToolTip(
                "立即结束当前发布任务，并停止本轮后续排队任务；浏览器保持打开。"
            )
        else:
            self.dashboard_publish_button.setText("▶  开始发布")
            self.dashboard_publish_button.setObjectName("ReferencePublishButton")
            self.dashboard_publish_button.setToolTip("开始执行全部待执行任务")

        # Force Qt stylesheet refresh after objectName changes.
        self.dashboard_publish_button.style().unpolish(
            self.dashboard_publish_button
        )
        self.dashboard_publish_button.style().polish(
            self.dashboard_publish_button
        )
        self.dashboard_publish_button.update()

    def _sync_publish_button_state(self):
        self._set_publish_button_running(self._has_active_publish_work())

    def _sync_reference_runtime(self):
        super()._sync_reference_runtime()
        self._sync_publish_button_state()

    def _current_task_changed(self, running: bool, task_id: int, code: str):
        # Preserve the inherited cancellation-state hook first.
        try:
            super()._current_task_changed(running, task_id, code)
        except Exception:
            pass

        # Do not immediately switch back to "开始发布" between two queued tasks.
        # The DB queue state determines whether the batch is still active.
        self._sync_publish_button_state()

    def _task_cancelled(self, tid: int, code: str, risky: bool, message: str):
        super()._task_cancelled(tid, code, risky, message)
        self._set_publish_button_running(False)

    def _queue_paused(self, reason: str):
        super()._queue_paused(reason)
        self._sync_publish_button_state()
