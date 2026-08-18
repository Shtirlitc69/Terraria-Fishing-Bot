from PySide6.QtCore import QObject, Signal


class BotBridge(QObject):
    """MemoryBot callbacks → Qt signals (connect with QueuedConnection)."""

    catch_id = Signal(int)
    status = Signal(str)
    error = Signal(str)
    aim_saved = Signal(int, int)

    def on_catch(self, item_id):
        self.catch_id.emit(int(item_id))

    def on_status(self, text):
        self.status.emit("" if text is None else str(text))

    def on_error(self, err):
        self.error.emit("" if err is None else str(err))

    def on_aim(self, xy):
        x, y = int(xy[0]), int(xy[1])
        self.aim_saved.emit(x, y)
