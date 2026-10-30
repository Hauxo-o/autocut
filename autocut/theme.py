"""Apparence de l'application : plusieurs themes (couleurs, boutons, cartes, curseurs...)."""
import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QPen, QPixmap

# ---------------------------------------------------------------- les themes
_DARK = dict(
    dark=True, bg="#14161b", header="#1a1d25", header_line="#2a2e39", card="#1c1f27", field="#12141a",
    border="#2a2e39", text="#e7eaf0", muted="#8a92a3", accent="#4f7cf0", accent_hover="#6890ff",
    accent_soft="#8fb0ff", on_accent="#ffffff", btn="#2a2f3b", btn_border="#363c4a", btn_hover="#343b4a",
    nav="#232731", wave_bg="#111318", wave="#5aa0eb", wave_dim="#4678b4", ruler="#1b1e26",
    cut="#eb3737", handle="#ffeb5a", scroll="#3a4050", scroll_hover="#4d566b", disabled="#5d6475",
    playhead="#ffffff", thr="#ffd250", checkbox="#4a5163",
)
_LIGHT = dict(
    dark=False, bg="#eceff4", header="#ffffff", header_line="#d3d8e2", card="#ffffff", field="#f4f6fa",
    border="#d3d8e2", text="#1c2330", muted="#667085", accent="#2f6bff", accent_hover="#4b82ff",
    accent_soft="#2457d6", on_accent="#ffffff", btn="#e6e9f0", btn_border="#cfd5e0", btn_hover="#dbe0ea",
    nav="#e9ecf3", wave_bg="#f6f8fc", wave="#3b82f6", wave_dim="#7aa7f5", ruler="#e3e7ee",
    cut="#e03c3c", handle="#e8a900", scroll="#c3cad8", scroll_hover="#a9b3c6", disabled="#9aa3b5",
    playhead="#10131a", thr="#d98a00", checkbox="#a9b3c6",
)

THEMES = {
    "nuit": dict(_DARK, label="Classique nuit"),
    "jour": dict(_LIGHT, label="Classique jour"),
    "violet": dict(
        _DARK, label="Nuit violet et rose", bg="#140f1f", header="#1b1428", header_line="#35294f", card="#1f1830",
        field="#150f23", border="#35294f", text="#f1eaff", muted="#a094c4", accent="#a259ff",
        accent_hover="#b87dff", accent_soft="#ff7ad9", btn="#2b2144", btn_border="#3d3060", btn_hover="#38295a",
        nav="#241b3a", wave_bg="#110c1b", wave="#c084fc", wave_dim="#8f5cc4", ruler="#1a1328", cut="#ff4d8d",
        handle="#ffe066", scroll="#3d3060", scroll_hover="#55438a", disabled="#655a85", checkbox="#5b4a85"),
    "bonbon": dict(
        _DARK, label="Nuit rose bonbon", bg="#1a0f16", header="#231320", header_line="#ff4fa3", card="#281724",
        field="#1c1019", border="#4a2640", text="#ffe9f4", muted="#d19ab9", accent="#ff4fa3",
        accent_hover="#ff74b8", accent_soft="#ff9ccc", on_accent="#2a0518", btn="#35192f", btn_border="#5a2c4d",
        btn_hover="#46223c", nav="#2d1a28", wave_bg="#170d14", wave="#ff7ab8", wave_dim="#c25d90", ruler="#211220",
        cut="#ff2e7e", handle="#ffe14d", scroll="#5a2c4d", scroll_hover="#7a3d68", disabled="#7d5a70",
        thr="#ffd1e6", checkbox="#7a3d68"),
    "rose": dict(
        _LIGHT, label="Rose bonbon (clair)", bg="#ffdcec", header="#ffc8e0", header_line="#f29bc0", card="#fff3f8",
        field="#ffffff", border="#f4a3c6", text="#5c0f35", muted="#b04f82", accent="#ff3d93", on_accent="#33051e",
        accent_hover="#ff62a9", accent_soft="#c9186f", btn="#ffd0e4", btn_border="#f29bc0", btn_hover="#ffc0da",
        nav="#ffe0ee", wave_bg="#fff0f7", wave="#d81b7a", wave_dim="#ff9ac7", ruler="#ffd9ea", cut="#d4004f",
        handle="#ffb000", scroll="#f3a5c8", scroll_hover="#ea86b3", disabled="#d9a0bb", playhead="#5c0f35",
        thr="#b8005a", checkbox="#e58ab3"),
    "metal": dict(
        _DARK, label="Argent, or et bronze", bg="#151618", header="#1d1e21", header_line="#3b3c40", card="#212225",
        field="#18191b", border="#3b3c40", text="#e6e4df", muted="#9c9ca2", accent="#d4a64a",
        accent_hover="#e8bf68", accent_soft="#cd8f5a", on_accent="#1d1606", btn="#2c2d31", btn_border="#46474c",
        btn_hover="#3a3b40", nav="#26272a", wave_bg="#121315", wave="#c3c7d0", wave_dim="#888c96", ruler="#1c1d20",
        cut="#c8582e", handle="#f2c75c", scroll="#46474c", scroll_hover="#5f6068", disabled="#66676d",
        checkbox="#5a5b61"),
    "japon": dict(
        _LIGHT, label="Japonais (washi et vermillon)", bg="#f2ead9", header="#e9dec6", header_line="#c3382c",
        card="#faf5ea", field="#fffdf8", border="#d6c8aa", text="#2a2220", muted="#7a6a58", accent="#c3382c",
        accent_hover="#d9493c", accent_soft="#2c4a6e", on_accent="#fff8ee", btn="#eadfc7", btn_border="#cdbd9c",
        btn_hover="#e0d2b3", nav="#efe5cf", wave_bg="#f6efdf", wave="#2c4a6e", wave_dim="#6f88a6", ruler="#e6dcc4",
        cut="#c3382c", handle="#d9a21b", scroll="#cdbd9c", scroll_hover="#b8a67f", disabled="#b3a58d",
        playhead="#2a2220", thr="#b87a00", checkbox="#b8a67f"),
    "deathnote": dict(
        _DARK, label="Death Note", logo_font="Georgia", bg="#0a0a0b", header="#111113", header_line="#b3121b",
        card="#161618", field="#0e0e10", border="#2b2b2f", text="#ece7dc", muted="#8f8c85", accent="#b3121b",
        accent_hover="#d41c28", accent_soft="#d8d2c4", on_accent="#fff4f2", btn="#202023", btn_border="#3a3a3f",
        btn_hover="#2c2c31", nav="#19191c", wave_bg="#0c0c0e", wave="#d8d2c4", wave_dim="#7e7b74", ruler="#131315",
        cut="#c40d1a", handle="#f5f0e6", scroll="#3a3a3f", scroll_hover="#55555c", disabled="#5a5852",
        playhead="#ffffff", thr="#d41c28", checkbox="#55555c"),
    "matrix": dict(
        _DARK, label="Matrix", font="Consolas", radius=2, bg="#020b05", header="#04140a", header_line="#00ff66",
        card="#06180d", field="#020b05", border="#0b3d1d", text="#b6ffcf", muted="#4fbf7a", accent="#00e05a",
        accent_hover="#33ff85", accent_soft="#66ffa6", on_accent="#00140a", btn="#0a2a16", btn_border="#0f5a2c",
        btn_hover="#0f3d20", nav="#071d0f", wave_bg="#010703", wave="#00ff66", wave_dim="#08994a", ruler="#04140a",
        cut="#ff2d6f", handle="#f6ff00", scroll="#0f5a2c", scroll_hover="#18873f", disabled="#2f6b46",
        thr="#f6ff00", checkbox="#18873f"),
    "cyberpunk": dict(
        _DARK, label="Cyberpunk", bg="#0a0a1f", header="#10102b", header_line="#fcee09", card="#12122e",
        field="#0b0b22", border="#2a2a5c", text="#e8f0ff", muted="#7f8fd0", accent="#fcee09",
        accent_hover="#fff45c", accent_soft="#00f0ff", on_accent="#101010", btn="#1c1c45", btn_border="#3a3a85",
        btn_hover="#28285f", nav="#15153a", wave_bg="#080818", wave="#00f0ff", wave_dim="#0a8d99", ruler="#10102b",
        cut="#ff2a6d", handle="#fcee09", scroll="#3a3a85", scroll_hover="#5555b5", disabled="#55598c",
        thr="#ff2a6d", checkbox="#5555b5"),
    "vaporwave": dict(
        _DARK, label="Vaporwave", radius=14, bg="#1a0b3d", header="#240f52", header_line="#ff71ce", card="#2a1260",
        field="#160a35", border="#4b2a96", text="#fdf0ff", muted="#b9a2ff", accent="#ff71ce", accent_hover="#ff94da",
        accent_soft="#01cdfe", on_accent="#1a0b3d", btn="#35187a", btn_border="#5b36b0", btn_hover="#442096",
        nav="#2a1260", wave_bg="#140930", wave="#05ffa1", wave_dim="#03a86a", ruler="#240f52", cut="#ff71ce",
        handle="#fffb96", scroll="#5b36b0", scroll_hover="#7b52d6", disabled="#7a64b8", thr="#01cdfe",
        checkbox="#7b52d6"),
    "abyss": dict(
        _DARK, label="Abysses", bg="#00121f", header="#021c2e", header_line="#00e5c3", card="#04263b",
        field="#01182a", border="#0b4766", text="#d6f6ff", muted="#6fa8c0", accent="#00e5c3",
        accent_hover="#4dffe0", accent_soft="#5ad1ff", on_accent="#00221c", btn="#073553", btn_border="#0f5c85",
        btn_hover="#0b4a70", nav="#052f48", wave_bg="#000e18", wave="#19d3da", wave_dim="#0f8e96", ruler="#021c2e",
        cut="#ff5a7a", handle="#ffe066", scroll="#0f5c85", scroll_hover="#1a80b5", disabled="#3f7a96",
        thr="#ffe066", checkbox="#1a80b5"),
    "lava": dict(
        _DARK, label="Volcan", bg="#120504", header="#1c0a07", header_line="#ff6a00", card="#230d08",
        field="#170705", border="#4a1d10", text="#ffe9d6", muted="#c98a6b", accent="#ff6a00",
        accent_hover="#ff8a33", accent_soft="#ffc300", on_accent="#1a0600", btn="#3a1409", btn_border="#6a2a14",
        btn_hover="#4d1b0d", nav="#2b0f09", wave_bg="#0e0403", wave="#ff8a00", wave_dim="#b25400", ruler="#1c0a07",
        cut="#ff1f1f", handle="#ffe14d", scroll="#6a2a14", scroll_hover="#944020", disabled="#8c5a45",
        thr="#ffe14d", checkbox="#944020"),
    "toxic": dict(
        _DARK, label="Toxic", font="Trebuchet MS", radius=6, bg="#0b0f00", header="#131a00", header_line="#b6ff00",
        card="#161e00", field="#0a0d00", border="#37470a", text="#eaffb0", muted="#9db84a", accent="#b6ff00",
        accent_hover="#ccff44", accent_soft="#d9ff66", on_accent="#0b0f00", btn="#222d03", btn_border="#4a6310",
        btn_hover="#2f3d05", nav="#1a2300", wave_bg="#080b00", wave="#b6ff00", wave_dim="#6f9a00", ruler="#131a00",
        cut="#ff6a00", handle="#fff200", scroll="#4a6310", scroll_hover="#6c8f18", disabled="#5d6b2a",
        thr="#ff6a00", checkbox="#6c8f18"),
    "amber": dict(
        _DARK, label="Ambre CRT", logo_font="Consolas", bg="#100800", header="#170d00", header_line="#ffb000",
        card="#1c1000", field="#120a00", border="#4a2f00", text="#ffcf70", muted="#b07a1a", accent="#ffb000",
        accent_hover="#ffc84d", accent_soft="#ff8c00", on_accent="#1a0e00", btn="#2e1b00", btn_border="#5e3b00",
        btn_hover="#402600", nav="#231400", wave_bg="#0c0600", wave="#ffb000", wave_dim="#a06e00", ruler="#170d00",
        cut="#ff4500", handle="#fff2b0", scroll="#5e3b00", scroll_hover="#8a5700", disabled="#7a5a20",
        thr="#fff2b0", checkbox="#8a5700"),
    "gameboy": dict(
        _LIGHT, label="Game Boy", font="Courier New", radius=2, bg="#8bac0f", header="#7d9e0d", header_line="#0f380f",
        card="#9bbc0f", field="#a9c92a", border="#306230", text="#0f380f", muted="#306230", accent="#306230",
        accent_hover="#0f380f", accent_soft="#0f380f", on_accent="#9bbc0f", btn="#8bac0f", btn_border="#306230",
        btn_hover="#7c9c0b", nav="#8bac0f", wave_bg="#9bbc0f", wave="#0f380f", wave_dim="#306230", ruler="#8bac0f",
        cut="#0f380f", handle="#f4ffb0", scroll="#306230", scroll_hover="#0f380f", disabled="#5f7f20",
        playhead="#0f380f", thr="#0f380f", checkbox="#306230"),
    "halloween": dict(
        _DARK, label="Halloween", radius=10, bg="#0f0817", header="#170c24", header_line="#ff7518", card="#1b1029",
        field="#0c0612", border="#3b2357", text="#ffe9d6", muted="#b794d6", accent="#ff7518", accent_hover="#ff9040",
        accent_soft="#b266ff", on_accent="#1a0a00", btn="#2a1740", btn_border="#4b2c70", btn_hover="#38205a",
        nav="#211233", wave_bg="#0a0510", wave="#ff9a3c", wave_dim="#b5651f", ruler="#170c24", cut="#9d00ff",
        handle="#76ff03", scroll="#4b2c70", scroll_hover="#6a3f9a", disabled="#6b5588", thr="#76ff03",
        checkbox="#6a3f9a"),
    "journal": dict(
        _LIGHT, label="Journal", logo_font="Georgia", bg="#e9e5d8", header="#f4f1e8", header_line="#111111",
        card="#f7f4ec", field="#fffdf6", border="#bdb7a5", text="#141414", muted="#6d6858", accent="#141414",
        accent_hover="#3a3a3a", accent_soft="#141414", on_accent="#f7f4ec", btn="#dedacb", btn_border="#a9a38f",
        btn_hover="#d0cbb9", nav="#e2decf", wave_bg="#f1eee3", wave="#222222", wave_dim="#77735f", ruler="#dedacb",
        cut="#c0121a", handle="#f2c200", scroll="#a9a38f", scroll_hover="#7d7867", disabled="#a39e8c",
        playhead="#111111", thr="#c0121a", checkbox="#8a8574"),
    "win95": dict(
        _LIGHT, label="Windows 95", font="Tahoma", radius=0, header_text="#ffffff", bg="#c0c0c0", header="#000080",
        header_line="#808080", card="#c0c0c0", field="#ffffff", border="#808080", text="#000000", muted="#404040",
        accent="#000080", accent_hover="#1010a0", accent_soft="#000080", on_accent="#ffffff", btn="#c0c0c0",
        btn_border="#404040", btn_hover="#d4d0c8", nav="#c0c0c0", wave_bg="#000000", wave="#00ff00",
        wave_dim="#008000", ruler="#d4d0c8", cut="#ff0000", handle="#ffff00", scroll="#808080",
        scroll_hover="#606060", disabled="#808080", playhead="#ffffff", thr="#ffff00", checkbox="#404040"),
    "hotdog": dict(
        _LIGHT, label="Hot Dog Stand", font="Comic Sans MS", radius=0, header_text="#ffffff", bg="#ffe600",
        header="#e00000", header_line="#000000", card="#fff27a", field="#ffffff", border="#000000", text="#000000",
        muted="#7a0000", accent="#e00000", accent_hover="#ff2a2a", accent_soft="#a00000", on_accent="#ffffff",
        btn="#ffd000", btn_border="#000000", btn_hover="#ffe34d", nav="#fff27a", wave_bg="#fff9b0", wave="#e00000",
        wave_dim="#ff7a7a", ruler="#ffe600", cut="#0000ff", handle="#000000", scroll="#000000",
        scroll_hover="#444444", disabled="#9a8a00", playhead="#000000", thr="#0000ff", checkbox="#000000"),
    "pirate": dict(
        _LIGHT, label="Pirate", font="Ink Free", radius=6, header_text="#fbe9c0", bg="#e3cf9f", header="#5b3a1e",
        header_line="#c9a227", card="#f1e2b8", field="#fbf3d9", border="#a88b4a", text="#3a2410", muted="#7a5a2c",
        accent="#8b1a1a", accent_hover="#a52a2a", accent_soft="#6b3e12", on_accent="#fbe9c0", btn="#e6d19a",
        btn_border="#a88b4a", btn_hover="#dcc283", nav="#eadba8", wave_bg="#f6ecc8", wave="#5b3a1e",
        wave_dim="#a88b4a", ruler="#e0cc96", cut="#8b1a1a", handle="#c9a227", scroll="#a88b4a",
        scroll_hover="#8a6d33", disabled="#b09a66", playhead="#3a2410", thr="#8b1a1a", checkbox="#8a6d33"),
    "dracula": dict(
        _DARK, label="Dracula", radius=8, bg="#282a36", header="#21222c", header_line="#bd93f9", card="#2f3242",
        field="#21222c", border="#44475a", text="#f8f8f2", muted="#9aa3d6", accent="#bd93f9", accent_hover="#cfa9ff",
        accent_soft="#ff79c6", on_accent="#1e1f29", btn="#3a3d4f", btn_border="#50536a", btn_hover="#454860",
        nav="#2b2d3b", wave_bg="#1f2029", wave="#8be9fd", wave_dim="#5aa8c0", ruler="#21222c", cut="#ff5555",
        handle="#f1fa8c", scroll="#50536a", scroll_hover="#6a6e8c", disabled="#6c78b0", thr="#ffb86c",
        checkbox="#6c78b0"),
    "nord": dict(
        _DARK, label="Nord", radius=6, bg="#2e3440", header="#292e39", header_line="#88c0d0", card="#3b4252",
        field="#2b303b", border="#4c566a", text="#eceff4", muted="#a9b3c8", accent="#88c0d0", accent_hover="#9fd0de",
        accent_soft="#81a1c1", on_accent="#1f2530", btn="#434c5e", btn_border="#5a657a", btn_hover="#4c566a",
        nav="#353c4a", wave_bg="#272c36", wave="#88c0d0", wave_dim="#5e81ac", ruler="#2b303b", cut="#bf616a",
        handle="#ebcb8b", scroll="#5a657a", scroll_hover="#7684a0", disabled="#6c778e", thr="#d08770",
        checkbox="#6c778e"),
    "gruvbox": dict(
        _DARK, label="Gruvbox", radius=4, bg="#282828", header="#1d2021", header_line="#d79921", card="#32302f",
        field="#1d2021", border="#504945", text="#ebdbb2", muted="#a89984", accent="#d79921", accent_hover="#fabd2f",
        accent_soft="#fe8019", on_accent="#1d2021", btn="#3c3836", btn_border="#665c54", btn_hover="#504945",
        nav="#2e2c2b", wave_bg="#1d2021", wave="#b8bb26", wave_dim="#79740e", ruler="#1d2021", cut="#fb4934",
        handle="#fabd2f", scroll="#665c54", scroll_hover="#7c6f64", disabled="#7c6f64", thr="#fe8019",
        checkbox="#7c6f64"),
    "solard": dict(
        _DARK, label="Solarized dark", radius=4, bg="#002b36", header="#00212b", header_line="#2aa198", card="#073642",
        field="#00252f", border="#0e4b59", text="#a3b1b1", muted="#86999b", accent="#268bd2", accent_hover="#4aa3e6",
        accent_soft="#2aa198", on_accent="#fdf6e3", btn="#0a3d4a", btn_border="#175b6b", btn_hover="#0f4a59",
        nav="#04303b", wave_bg="#00212b", wave="#2aa198", wave_dim="#1c6e68", ruler="#00212b", cut="#dc322f",
        handle="#b58900", scroll="#175b6b", scroll_hover="#24798d", disabled="#586e75", thr="#cb4b16",
        checkbox="#2f7f93"),
    "solarl": dict(
        _LIGHT, label="Solarized light", radius=4, bg="#fdf6e3", header="#eee8d5", header_line="#93a1a1",
        card="#f7f0dc", field="#fffaf0", border="#d9d2bd", text="#506870", muted="#74888a", accent="#268bd2",
        accent_hover="#4aa3e6", accent_soft="#1c6fa8", on_accent="#fdf6e3", btn="#eee8d5", btn_border="#d3cbb0",
        btn_hover="#e5dec4", nav="#f1ead6", wave_bg="#fffaf0", wave="#268bd2", wave_dim="#7fb5de", ruler="#eee8d5",
        cut="#dc322f", handle="#b58900", scroll="#cfc7ac", scroll_hover="#b5ad92", disabled="#a9b2ae",
        playhead="#073642", thr="#cb4b16", checkbox="#93a1a1"),
    "tokyo": dict(
        _DARK, label="Tokyo Night", radius=8, bg="#1a1b26", header="#16161e", header_line="#7aa2f7", card="#1f2335",
        field="#16161e", border="#2f334d", text="#c0caf5", muted="#8590c0", accent="#7aa2f7", accent_hover="#9ab8ff",
        accent_soft="#bb9af7", on_accent="#16161e", btn="#292e42", btn_border="#3b4261", btn_hover="#33395a",
        nav="#202336", wave_bg="#13141c", wave="#7dcfff", wave_dim="#4a89b0", ruler="#16161e", cut="#f7768e",
        handle="#e0af68", scroll="#3b4261", scroll_hover="#545c8c", disabled="#5f6993", thr="#ff9e64",
        checkbox="#545c8c"),
    "sakura": dict(
        _LIGHT, label="Sakura", radius=12, bg="#fff1f5", header="#ffe0ea", header_line="#e8799b", card="#fff8fa",
        field="#ffffff", border="#f2c4d2", text="#4a2a36", muted="#946678", accent="#d04b78", accent_hover="#e0668f",
        accent_soft="#b8456f", on_accent="#ffffff", btn="#ffe4ec", btn_border="#f0b8ca", btn_hover="#ffd5e1",
        nav="#fff0f4", wave_bg="#fffafb", wave="#d04b78", wave_dim="#f2b3c6", ruler="#ffe9f0", cut="#8e2a54",
        handle="#7c9a3c", scroll="#efb9ca", scroll_hover="#e29bb3", disabled="#d3b6c0", playhead="#4a2a36",
        thr="#8e2a54", checkbox="#d99ab0"),
    "coffee": dict(
        _DARK, label="Coffee", radius=8, bg="#1f1612", header="#2a1d17", header_line="#c8955a", card="#2b1f19",
        field="#1a120e", border="#4a352a", text="#f0e0cc", muted="#b89a82", accent="#c8955a", accent_hover="#dcab72",
        accent_soft="#e0b887", on_accent="#1f1209", btn="#3a2a21", btn_border="#5c4332", btn_hover="#48352a",
        nav="#261b15", wave_bg="#170f0b", wave="#e0b887", wave_dim="#9a7650", ruler="#2a1d17", cut="#d9534f",
        handle="#fff1c9", scroll="#5c4332", scroll_hover="#7a5b44", disabled="#7d6555", thr="#f0a35e",
        checkbox="#7a5b44"),
    "forest": dict(
        _DARK, label="Forest", radius=10, bg="#0f1a14", header="#14231b", header_line="#6fbf73", card="#17271e",
        field="#0c150f", border="#2b4535", text="#dcefdc", muted="#8fb596", accent="#6fbf73", accent_hover="#8bd68f",
        accent_soft="#a5d6a7", on_accent="#08140b", btn="#1f3527", btn_border="#35563f", btn_hover="#294532",
        nav="#13211a", wave_bg="#0a120d", wave="#81c784", wave_dim="#4a8a50", ruler="#14231b", cut="#e57373",
        handle="#ffd54f", scroll="#35563f", scroll_hover="#4c7a58", disabled="#5b7a63", thr="#ffb74d",
        checkbox="#4c7a58"),
    "blueprint": dict(
        _DARK, label="Blueprint", radius=0, bg="#0d2a4d", header="#0a2140", header_line="#7fb8ff", card="#12345c",
        field="#0a2140", border="#2c5a92", text="#e8f1ff", muted="#9bbbe3", accent="#7fb8ff", accent_hover="#a3cdff",
        accent_soft="#d6e8ff", on_accent="#06182e", btn="#17406d", btn_border="#3a6ba5", btn_hover="#1e4d82",
        nav="#0f2f55", wave_bg="#0a2140", wave="#e8f1ff", wave_dim="#7fa6d6", ruler="#0a2140", cut="#ff6b6b",
        handle="#ffe066", scroll="#3a6ba5", scroll_hover="#5588c2", disabled="#6a8cb8", thr="#ffe066",
        checkbox="#5588c2"),
    "chalkboard": dict(
        _DARK, label="Chalkboard", radius=14, bg="#2b3a35", header="#24312d", header_line="#f2c94c", card="#31423c",
        field="#25322e", border="#4d625a", text="#f5f1e6", muted="#b9c4bd", accent="#f2c94c", accent_hover="#ffd965",
        accent_soft="#ffadad", on_accent="#2b3a35", btn="#3b4e47", btn_border="#5d746b", btn_hover="#48605a",
        nav="#2f403a", wave_bg="#232f2b", wave="#f5f1e6", wave_dim="#a9b1aa", ruler="#24312d", cut="#ff7b7b",
        handle="#f2c94c", scroll="#5d746b", scroll_hover="#7b948a", disabled="#8a9a92", thr="#f2c94c",
        checkbox="#7b948a"),
    "steampunk": dict(
        _DARK, label="Steampunk", radius=3, bg="#1c1612", header="#2a2018", header_line="#b87333", card="#2a2119",
        field="#17110d", border="#5a4631", text="#efe0c0", muted="#b49c78", accent="#b87333", accent_hover="#d18a48",
        accent_soft="#d4af37", on_accent="#150d06", btn="#3a2e22", btn_border="#6b543a", btn_hover="#483a2b",
        nav="#251d16", wave_bg="#140f0b", wave="#d4af37", wave_dim="#8a7220", ruler="#2a2018", cut="#c0392b",
        handle="#7fdbca", scroll="#6b543a", scroll_hover="#8a6e4b", disabled="#85755a", thr="#7fdbca",
        checkbox="#8a6e4b"),
    "space": dict(
        _DARK, label="Space", radius=10, bg="#080b1e", header="#0d1130", header_line="#7c4dff", card="#10153a",
        field="#060818", border="#262c63", text="#e6e9ff", muted="#98a0e0", accent="#7c4dff", accent_hover="#9a73ff",
        accent_soft="#ff80ab", on_accent="#ffffff", btn="#1a2050", btn_border="#303a85", btn_hover="#232b66",
        nav="#0e1334", wave_bg="#050716", wave="#80d8ff", wave_dim="#4a9bc2", ruler="#0d1130", cut="#ff4081",
        handle="#ffea00", scroll="#303a85", scroll_hover="#4954b0", disabled="#6a72b8", thr="#ff80ab",
        checkbox="#4954b0"),
    "ice": dict(
        _LIGHT, label="Ice", radius=12, bg="#eaf6fb", header="#d7eef8", header_line="#5bb5d9", card="#f6fcff",
        field="#ffffff", border="#bfe0ef", text="#12334a", muted="#557a90", accent="#2aa5d6", accent_hover="#4cb9e4",
        accent_soft="#1581ac", on_accent="#08202e", btn="#dff1f9", btn_border="#b3d8e8", btn_hover="#cfe9f5",
        nav="#e6f5fb", wave_bg="#f3fbff", wave="#1b86b3", wave_dim="#8fd0e8", ruler="#d7eef8", cut="#d6455a",
        handle="#f0a500", scroll="#aad3e4", scroll_hover="#86bdd3", disabled="#9db8c4", playhead="#12334a",
        thr="#1581ac", checkbox="#7fb3c9"),
    "sunset": dict(
        _DARK, label="Sunset", radius=12, bg="#1d1020", header="#2a1428", header_line="#ff7e5f", card="#2a1730",
        field="#170b1b", border="#4d2a4f", text="#ffeadb", muted="#d6a9b8", accent="#ff7e5f", accent_hover="#ff9a80",
        accent_soft="#feb47b", on_accent="#2a0e10", btn="#3a1d3c", btn_border="#62345f", btn_hover="#492449",
        nav="#251329", wave_bg="#150a18", wave="#feb47b", wave_dim="#b97a52", ruler="#2a1428", cut="#ff3d6e",
        handle="#ffe66d", scroll="#62345f", scroll_hover="#84487f", disabled="#8a6275", thr="#ffe66d",
        checkbox="#84487f"),
}

# Police de chaque theme : elle doit rappeler l'ambiance du theme (toutes sont fournies avec Windows).
FONTS = {
    "nuit": "Segoe UI", "jour": "Calibri", "violet": "Trebuchet MS", "bonbon": "Segoe UI Semibold",
    "rose": "Corbel", "metal": "Constantia", "japon": "Yu Gothic UI", "deathnote": "Georgia",
    "matrix": "Consolas", "cyberpunk": "Bahnschrift", "vaporwave": "Verdana", "abyss": "Lucida Sans Unicode",
    "lava": "Rockwell", "toxic": "Franklin Gothic Medium", "amber": "Courier New", "gameboy": "Lucida Console",
    "halloween": "Segoe Print", "journal": "Times New Roman", "win95": "Tahoma", "hotdog": "Comic Sans MS",
    "pirate": "Ink Free", "dracula": "Cascadia Code", "nord": "Calibri", "gruvbox": "Cambria",
    "solard": "Consolas", "solarl": "Constantia", "tokyo": "Bahnschrift", "sakura": "Candara",
    "coffee": "Palatino Linotype", "forest": "Sitka Text", "blueprint": "Consolas", "chalkboard": "Segoe Print",
    "steampunk": "Book Antiqua", "space": "Century Gothic", "ice": "Segoe UI", "sunset": "Candara",
}
# Certaines polices sont plus larges ou plus petites que Segoe UI : on corrige leur taille pour que
# l'interface garde les memes proportions dans tous les themes.
FONT_SCALE = {"Verdana": 0.92, "Consolas": 0.94, "Courier New": 0.9, "Lucida Console": 0.88, "Segoe Print": 0.9,
              "Comic Sans MS": 0.94, "Century Gothic": 0.93, "Cascadia Code": 0.9, "Ink Free": 1.06,
              "Palatino Linotype": 0.97, "Book Antiqua": 0.97, "Rockwell": 0.94, "Georgia": 0.96,
              "Lucida Sans Unicode": 0.95, "Candara": 1.04, "Corbel": 1.03, "Calibri": 1.06, "Constantia": 0.99,
              "Cambria": 0.99, "Times New Roman": 1.04, "Sitka Text": 0.95, "Franklin Gothic Medium": 1.0}
for _k, _f in FONTS.items():
    if _k in THEMES:
        THEMES[_k]["font"] = _f

_current = "nuit"

# polices : Segoe UI, puis des polices qui contiennent le japonais / chinois si besoin
FONT_FAMILIES = ["Segoe UI", "Yu Gothic UI", "Meiryo UI", "Segoe UI Symbol", "Microsoft YaHei UI"]


def current_name():
    return _current


def T():
    """Le theme actuel (dictionnaire de couleurs)."""
    return THEMES[_current]


def col(key, alpha=None):
    c = QColor(T()[key])
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def _rgba(hex_color, a):
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"


def _check_icon(color):
    """Petite coche pour les cases cochees (creee une fois dans le dossier temporaire)."""
    path = os.path.join(tempfile.gettempdir(), f"autocut_check_{color.lstrip('#')}.png")
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(4, 8, 7, 11)
    p.drawLine(7, 11, 12, 5)
    p.end()
    pm.save(path)
    return path.replace("\\", "/")


def build_qss(t):
    check = _check_icon(t["on_accent"])
    R = int(t.get("radius", 8))                 # rondeur des coins (0 = carre)
    rf, rg, rcard, ri = max(0, R - 1), round(R * 1.5), round(R * 1.75), max(0, R - 3)
    htr = (f"QFrame#header QLabel {{ color: {t['header_text']}; }}" if t.get("header_text") else "")
    return f"""
QMainWindow, QDialog, QMessageBox {{ background: {t['bg']}; }}
QWidget#root {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

QFrame#header {{ background: {t['header']}; border-bottom: 2px solid {t['header_line']}; }}
QLabel#logo {{ font-size: 16pt; font-weight: 700; font-family: "{t.get('logo_font', t.get('font', 'Segoe UI'))}"; }}
QLabel#muted {{ color: {t['muted']}; }}
QLabel#fileinfo {{ color: {t['muted']}; }}
QLabel#summary {{ padding: 2px 0 4px 0; }}
{htr}
QLabel#helptitle {{ font-size: 18pt; font-weight: 700; }}
QLabel#cardtitle {{ font-size: 11.5pt; font-weight: 700; }}
QLabel#badge {{ background: {t['accent']}; color: {t['on_accent']}; border-radius: 12px; font-weight: 700;
               min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; qproperty-alignment: AlignCenter; }}
QLabel#chip {{ background: {t['btn']}; border: 1px solid {t['btn_border']}; border-radius: {rf}px; padding: 2px 8px;
              font-weight: 600; }}

QFrame#card {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: {rcard}px; }}
QGroupBox {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: {rg}px;
            margin-top: 16px; padding: 16px 12px 12px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 8px; color: {t['accent_soft']}; }}

QPushButton {{ background: {t['btn']}; border: 1px solid {t['btn_border']}; border-radius: {R}px; padding: 7px 14px; }}
QPushButton:hover {{ background: {t['btn_hover']}; border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:disabled {{ color: {t['disabled']}; background: {t['nav']}; border-color: {t['border']}; }}
QPushButton[on="true"] {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton[on="true"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#primary {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {t['border']}; border-color: {t['border']}; color: {t['disabled']}; }}
QPushButton#nav {{ padding: 5px 3px; font-size: 9pt; background: {t['nav']}; border-color: {t['border']}; }}
QPushButton#nav:hover {{ background: {t['btn_hover']}; }}
QPushButton#nav:disabled {{ color: {t['disabled']}; }}
QPushButton#icon {{ padding: 7px 10px; }}
QPushButton#tool {{ padding: 1px 2px; font-size: 11pt; background: {t['nav']}; border-color: {t['border']}; }}
QPushButton#tool:hover {{ background: {t['btn_hover']}; }}
QPushButton#tool:checked, QPushButton#tool:checked:hover {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; }}
QPushButton#tool:disabled {{ color: {t['disabled']}; }}
QPushButton#tab {{ background: transparent; border: none; border-bottom: 3px solid transparent; border-radius: 0;
                  padding: 9px 18px; font-weight: 700; color: {t['muted']}; }}
QPushButton#tab:hover {{ background: {t['btn_hover']}; border: none; border-bottom: 3px solid {t['border']}; color: {t['text']}; }}
QPushButton#tab:checked, QPushButton#tab:checked:hover {{ background: transparent; color: {t['text']};
                  border: none; border-bottom: 3px solid {t['accent']}; }}
QLabel#sub {{ font-weight: 700; color: {t['accent_soft']}; padding-top: 4px; }}
QProgressBar {{ background: {t['border']}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
QPushButton#panelarrow {{ padding: 0px; font-size: 9pt; background: {t['nav']}; border: 1px solid {t['border']};
                         border-radius: {rf}px; color: {t['muted']}; }}
QPushButton#panelarrow:hover {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; }}

QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit {{ background: {t['field']}; border: 1px solid {t['btn_border']}; border-radius: {rf}px; padding: 5px 8px; }}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {t['card']}; border: 1px solid {t['btn_border']}; outline: 0;
                              selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; padding: 3px; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {t['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['text']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {t['accent_hover']}; }}

QTableWidget {{ background: {t['field']}; border: 1px solid {t['border']}; border-radius: {rf}px; outline: 0;
               selection-background-color: {_rgba(t['accent'], 90)}; selection-color: {t['text']}; }}
QTableWidget::item {{ padding: 3px 4px; border: none; }}
QHeaderView::section {{ background: {t['card']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['border']};
                       padding: 6px 4px; font-weight: 600; }}

QCheckBox, QRadioButton {{ spacing: 9px; }}
QCheckBox::indicator, QTableView::indicator {{ width: 16px; height: 16px; border: 1px solid {t['checkbox']};
                                              border-radius: {ri}px; background: {t['field']}; }}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']};
                                                              image: url({check}); }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 2px solid {t['checkbox']}; border-radius: 9px; background: {t['field']}; }}
QRadioButton::indicator:hover {{ border-color: {t['accent']}; }}
QRadioButton::indicator:checked {{ border: 2px solid {t['accent']}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
    fx:0.5, fy:0.5, stop:0 {t['accent']}, stop:0.3 {t['accent']}, stop:0.36 {t['field']}, stop:1 {t['field']}); }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['scroll_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['scroll']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background: {t['bg']}; }}
QSplitter#vsplit::handle:vertical {{ background: {t['border']}; margin: 3px 60px; border-radius: 1px; }}
QSplitter#vsplit::handle:vertical:hover {{ background: {t['accent']}; }}
QStatusBar {{ background: {t['header']}; color: {t.get('header_text', t['muted'])}; border-top: 1px solid {t['border']}; }}
QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['btn_border']}; padding: 5px; }}
QGraphicsView {{ border: none; }}
"""


def apply_theme(app, name=None, scale=None):
    """Applique un theme (et eventuellement la taille du texte, en %) a toute l'application."""
    global _current
    if name in THEMES:
        _current = name
    t = THEMES[_current]
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["bg"]))
    p.setColor(QPalette.WindowText, QColor(t["text"]))
    p.setColor(QPalette.Base, QColor(t["field"]))
    p.setColor(QPalette.AlternateBase, QColor(t["card"]))
    p.setColor(QPalette.Text, QColor(t["text"]))
    p.setColor(QPalette.Button, QColor(t["btn"]))
    p.setColor(QPalette.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.ToolTipBase, QColor(t["card"]))
    p.setColor(QPalette.ToolTipText, QColor(t["text"]))
    p.setColor(QPalette.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(t["on_accent"]))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(t["disabled"]))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["disabled"]))
    app.setPalette(p)
    if scale is not None or app.property("autocut_scale") is None:
        app.setProperty("autocut_scale", scale or 100)
    pct = app.property("autocut_scale") or 100
    fam = t.get("font", "Segoe UI")
    if fam not in QFontDatabase.families():     # police absente : on garde Segoe UI
        fam = "Segoe UI"
    f = QFont(fam)
    f.setPointSizeF(max(8.0, 10 * pct / 100 * FONT_SCALE.get(fam, 1.0)))
    f.setFamilies([fam] + [x for x in FONT_FAMILIES if x != fam])
    app.setFont(f)
    app.setStyleSheet(build_qss(t))
