# Modern Ping Widget

A sleek, desktop widget for monitoring network latency in real-time. Built with Python and CustomTkinter, it features a modern UI, historical graphing, and a "Zen Mode" for minimal distraction.

## Features

- **Real-Time Monitoring**: Continuously pings a target host (default: google.com) to track latency.
- **Visual Graph**: Displays a live bar chart of recent ping history.
- **Zen Mode**: double-click or auto-hide to switch to a compact, non-intrusive floating window that shows only the essential numbers.
- **System Tray Integration**: Run in the background and control via the system tray.
- **Customizable**:
  - Resizable window.
  - "Stay on top" mode.
  - Configurable refresh interval and target host.
  - Auto-save settings.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd "ping widget"
    ```

2.  **Create a virtual environment (optional):**
    ```bash
    python -m venv venv
    # Windows
    .\venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage

Run the widget:
```bash
python ping_widget/ping_widget.py
```

### Controls
- **Double-click**: Toggle "Zen Mode".
- **Right-click / Drag**: Move the window (standard OS behavior).
- **Settings**: Click the "Settings" button to adjust refresh rate and auto-hide timer.

## Configuration

Settings are automatically saved to `~/.PingWidgetModern.json`.

## License

MIT License. See [LICENSE](LICENSE) for details.
