"""示例插件：在面板上加一个按钮，显示当前电量。"""


def register(ctx):
    def show():
        for dev in ctx.battery():
            state = dev["state"]
            level = dev["level"]
            text = f"{level}%" if state == "ok" else state
            print(f"{dev['label']}: {text}")

    ctx.add_menu_item("打印电量", show)
