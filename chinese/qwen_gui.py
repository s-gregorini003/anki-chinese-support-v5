import os

from aqt import mw
from aqt.qt import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
)

# IMPORTANT: The config for this add-on is managed under the package name used in __init__.
# Inside this module, __name__ will be 'chinese.qwen_gui', so we need to refer to the
# top-level add-on package name explicitly. Replace 'chinese' below if your package is named differently.
ADDON_PACKAGE_NAME = "chinese"


class QwenVoiceSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Qwen3-TTS Voice Setup")

        layout = QVBoxLayout(self)

        info = (
            "Select a short recording of your voice and enter the exact text spoken "
            "in that recording. This will be sent to the Qwen3-TTS Hugging Face Space "
            "to create a cloned voice.\n\n"
            "Tip: 5–15 seconds of clean speech works best."
        )
        layout.addWidget(QLabel(info, self))

        # Reference audio path
        self.audio_path_edit = QLineEdit(self)
        self.audio_path_edit.setPlaceholderText("Path to reference audio file")
        layout.addWidget(self.audio_path_edit)

        browse_btn = QPushButton("Browse…", self)
        browse_btn.clicked.connect(self.browse_file)
        layout.addWidget(browse_btn)

        # Reference text
        self.ref_text_edit = QLineEdit(self)
        self.ref_text_edit.setPlaceholderText("Text spoken in the reference audio")
        layout.addWidget(self.ref_text_edit)

        # Save button
        save_btn = QPushButton("Save", self)
        save_btn.clicked.connect(self.save_config)
        layout.addWidget(save_btn)

        # Load existing config values if present
        config = mw.addonManager.getConfig(ADDON_PACKAGE_NAME)
        existing_path = config.get("qwen_ref_audio_path", "")
        existing_text = config.get("qwen_ref_text", "")
        self.audio_path_edit.setText(existing_path)
        self.ref_text_edit.setText(existing_text)

    def browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Reference Audio",
            "",
            "Audio Files (*.wav *.mp3 *.flac *.ogg);;All Files (*)",
        )
        if path:
            self.audio_path_edit.setText(path)

    def save_config(self):
        path = self.audio_path_edit.text().strip()
        text = self.ref_text_edit.text().strip()

        if not path or not os.path.exists(path):
            QMessageBox.warning(
                self,
                "Invalid audio file",
                "Please select an existing audio file.",
            )
            return

        if not text:
            QMessageBox.warning(
                self,
                "Missing reference text",
                "Please enter the text spoken in your reference audio.",
            )
            return

        config = mw.addonManager.getConfig(ADDON_PACKAGE_NAME)
        config["qwen_ref_audio_path"] = path
        config["qwen_ref_text"] = text
        mw.addonManager.writeConfig(ADDON_PACKAGE_NAME, config)

        QMessageBox.information(self, "Saved", "Qwen3-TTS reference voice saved.")
        self.accept()