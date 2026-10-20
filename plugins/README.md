# 插件目录

把 .py 文件丢进这个文件夹，重启「设备电量」后就会自动加载，不需要重新打包。

## 写一个插件

`python
def register(ctx):
    '''ctx 提供：add_menu_item(标签, 回调) / battery() / config() / toggle_swap()'''
    def hello():
        print('你好，我是插件')
    ctx.add_menu_item('我的插件', hello)
`

- ctx.add_menu_item(标签, 回调)：在面板底部加一个按钮
- ctx.battery()：返回当前电量列表 [{'key','label','state','level'}]
- ctx.config()：配置文件字典（改完自己调 config 保存）
- ctx.toggle_swap()：切换 WASD 互换，返回切换后的状态

## 注意

- 插件在启动时加载一次，改名/删除后重启生效
- 插件抛异常只会让它自己加载失败，不影响主程序
- 用到的第三方库要自己装（主程序本身零依赖）
