// AI-hint: Qt 6 Widgets fixture for tests/test-appmenu-global.py: a native Wayland QMainWindow whose QMenuBar Qt exports
// as com.canonical.dbusmenu (/MenuBar/<n>, created only while com.canonical.AppMenu.Registrar is on the bus) and, under
// a compositor with org_kde_kwin_appmenu (the MiOS mios-appmenu plugin), announces per surface. Menus: File (Open...
// Ctrl+O, a "Recent" submenu Qt fills lazily on aboutToShow, a disabled item), View (checkable "Show Grid" Ctrl+G, an
// exclusive Zoom radio group). --tool-window also maps a second toplevel WITHOUT a menubar: a window that must get no
// menu pills (main's exporter pairs the single /MenuBar export with every window of the process). Each triggered action
// is appended to the marker file as "<text>[ <checked>]".
// Build: g++ -std=c++17 -fPIC qt_menubar.cpp $(pkg-config --cflags --libs Qt6Widgets) -o qt_menubar
// AI-related: src/mios-rs/miosd/src/appmenu.rs, usr/share/mios/hyprland/plugins/mios-appmenu/main.cpp, tests/test-appmenu-global.py
#include <QActionGroup>
#include <QApplication>
#include <QFile>
#include <QLabel>
#include <QMainWindow>
#include <QMenu>
#include <QMenuBar>
#include <QTextStream>

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    const QStringList args = app.arguments();
    if (args.size() < 3) {
        QTextStream(stderr) << "usage: qt_menubar APP_ID MARKER_FILE [--tool-window]\n";
        return 2;
    }
    QGuiApplication::setDesktopFileName(args[1]); // the Wayland app_id
    const QString marker = args[2];
    auto mark = [marker](const QString& line) {
        QFile f(marker);
        if (f.open(QIODevice::Append | QIODevice::Text))
            QTextStream(&f) << line << "\n";
    };

    QMainWindow win;
    win.setWindowTitle("Qt menubar fixture");
    win.setCentralWidget(new QLabel("Qt menubar fixture"));
    win.resize(520, 340);
    QMenu* file = win.menuBar()->addMenu("&File");
    QAction* open = file->addAction("&Open…");
    open->setShortcut(QKeySequence("Ctrl+O"));
    QObject::connect(open, &QAction::triggered, [=] { mark("Open…"); });
    QMenu* recent = file->addMenu("&Recent");
    QObject::connect(recent, &QMenu::aboutToShow, [=] {
        if (!recent->isEmpty())
            return;
        QAction* one = recent->addAction("one.txt");
        QObject::connect(one, &QAction::triggered, [=] { mark("one.txt"); });
    });
    file->addSeparator();
    QAction* disabled = file->addAction("Disabled Item");
    disabled->setEnabled(false);

    QMenu* view = win.menuBar()->addMenu("&View");
    QAction* grid = view->addAction("Show &Grid");
    grid->setCheckable(true);
    grid->setChecked(true);
    grid->setShortcut(QKeySequence("Ctrl+G"));
    QObject::connect(grid, &QAction::triggered, [=](bool on) { mark(QString("Show Grid %1").arg(on ? "on" : "off")); });
    view->addSeparator();
    auto* zoom = new QActionGroup(&win);
    zoom->setExclusive(true);
    for (const char* label : {"Zoom 100%", "Zoom 200%"}) {
        QAction* a = view->addAction(label);
        a->setCheckable(true);
        a->setChecked(QString(label) == "Zoom 100%");
        zoom->addAction(a);
        QObject::connect(a, &QAction::triggered, [=] { mark(QString(label)); });
    }
    win.show();

    QWidget tool;
    if (args.contains("--tool-window")) {
        tool.setWindowTitle("Qt tool window");
        tool.resize(300, 200);
        tool.show();
    }
    return app.exec();
}
