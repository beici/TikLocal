"""Lightweight zh/en translations for templates and client scripts.

Locale resolution order: ``lang`` cookie (manual selection, per device) >
``Accept-Language`` header > Chinese default. The settings page writes both a
cookie and localStorage (``tiklocal:lang``) and reloads, so server-rendered
templates and client scripts always agree.
"""
from __future__ import annotations

import re

SUPPORTED_LOCALES = ('zh', 'en')
DEFAULT_LOCALE = 'zh'
LOCALE_COOKIE = 'lang'
_LANG_RE = re.compile(r'([a-zA-Z]{1,8})(?:-[a-zA-Z0-9]{1,8})*(?:;q=([0-9.]+))?')

# Key catalog, grouped by file domain so parallel edits stay mergeable.
# `zh` is the source of truth; `en` must define every key (tests enforce this).
TRANSLATIONS: dict[str, dict[str, str]] = {
    'zh': {
        # ---- common (base.html, shared JS) ----
        'common.app_name': 'tikLocal',
        'common.unknown_time': '未知时间',
        'common.cancel': '取消',
        'common.confirm': '确认',
        'common.close': '关闭',
        'common.delete': '删除',
        'common.save': '保存',
        'common.loading': '加载中…',
        'common.retry': '重试',
        'common.error.not_found': '请求的资源不存在。',
        'common.error.method_not_allowed': '该请求方式不被支持。',
        'common.error.internal': '服务器处理请求时出错，请稍后重试。',
        'common.error.storage_unavailable': '无法写入存储，请检查磁盘空间或权限后重试。',
        'common.error.offline': '无法连接服务器，请检查网络或确认 TikLocal 仍在运行。',
        'common.error.timeout': '服务器响应超时，请稍后重试。',
        'common.error.too_large': '上传内容超过 {limit} 上限，请压缩后再试。',
        'download.error.file_move_failed': '文件没能移入回收站，可能正被其他程序占用。',
        'download.error.canceled': '已取消。',
        'download.error.no_output': '下载结束，但没有文件进入媒体库。',
        'download.error.interrupted_restart': '任务因服务重启而中断。',
        'download.error.os_failure': '文件操作失败（{reason}，错误码 {errno}）。请确认磁盘空间与文件占用后重试。',
        'download.probe_failed': '检测下载组件失败，引擎可能暂时不可用。',
        'common.all': '全部',
        'common.search': '搜索',
        'common.open_settings': '打开设置',
        # ---- navigation ----
        'nav.home': '首页',
        'nav.flow': '播放',
        'nav.radio': '电台',
        'nav.library': '媒体库',
        'nav.saved': '收藏',
        'nav.download': '下载',
        'nav.settings': '设置',
        'nav.group.overview': '概览',
        'nav.group.experience': '体验',
        'nav.group.media': '媒体',
        'nav.sign_out': '退出登录',
        'nav.open_menu': '打开媒体与工具菜单',
        'nav.return_home': '返回首页',
        'nav.experience_mode': '体验模式',
        'nav.app_navigation': '应用导航',
        'nav.skip_to_content': '跳到主内容',
        'nav.main_navigation': '主导航',
        'nav.media_and_tools': '媒体与工具',
        'nav.tiklocal_home': 'TikLocal 首页',
        'nav.sign_out_label': '退出登录',
        # ---- page titles ----
        'title.download': '媒体导入',
        'title.saved': '收藏',
        'title.settings': '设置',
        'title.login': '登录',
        # ---- settings page ----
        'settings.section.appearance': '外观',
        'settings.section.radio_clients': '电台客户端',
        'settings.section.library': '媒体库',
        'settings.section.trash': '最近删除',
        'settings.section.privacy': '隐私',
        'settings.section.security': '访问密码',
        'settings.section.downloads': '下载',
        'settings.section.about': '关于',
        'settings.theme.light': '浅色',
        'settings.theme.dark': '深色',
        'settings.theme.system': '跟随系统',
        'settings.douyin.title': '抖音短视频模式',
        'settings.douyin.copy': '以抖音风格的导航、作者与文案浮层展示 Flow 信息流。',
        'settings.language.title': '语言',
        'settings.language.copy': '界面语言，立即生效。',
        'settings.language.zh': '中文',
        'settings.language.en': 'English',
        'settings.language.changed': '语言已切换',
'settings.language.cookie_blocked': '浏览器拦截了 Cookie，语言切换没有生效。请允许本站 Cookie 后重试。',
        # ---- settings page messages (JS) ----
        'settings.request_failed': '请求失败',
        'settings.sync.no_scan': '尚未完成扫描',
        'settings.sync.recent': '最近完成过一次扫描',
        'settings.sync.last': '上次扫描：{time}',
        'settings.sync.unavailable': '索引状态不可用',
        'settings.device.pair_unknown': '配对时间未知',
        'settings.device.paired': '配对于 {time}',
        'settings.device.none_title': '尚无电台客户端',
        'settings.device.none_copy': '在 TikLocal Radio 中输入本服务地址与访问密码完成配对。',
        'settings.device.token_active': '令牌有效',
        'settings.device.invalidated': '密码已更改而失效',
        'settings.device.revoke': '吊销',
        'settings.device.revoke_title': '吊销该电台客户端？',
        'settings.device.revoke_copy': '{name} 将失去媒体访问权限，需使用访问密码重新配对。',
        'settings.device.revoke_action': '吊销客户端',
        'settings.device.revoking': '正在吊销…',
        'settings.device.revoked_toast': '电台客户端已吊销',
        'settings.device.revoke_failed': '吊销失败，请稍后再试。',
        'settings.device.unavailable': '无法获取已配对设备。',
        'settings.device.this_device': '此设备',
        'settings.cred.saved_summary': '已保存 {count} 个凭据文件，并自动匹配到对应网站。',
        'settings.cred.none_summary': '尚未添加；公开内容仍可直接下载。',
        'settings.cred.none': '尚未保存凭据',
        'settings.dl.part_video': '视频',
        'settings.dl.part_image': '图片',
        'settings.dl.part_merge': '音视频合并',
        'settings.dl.not_ready': '{parts} 组件未就绪。',
        'settings.dl.ready': '图片与视频下载已就绪。',
        'settings.dl.status_unavailable': '无法获取下载状态。',
        'settings.dl.cred_unavailable': '无法获取网站凭据。',
        'settings.tiktok.not_configured': '未配置服务地址，按博主批量下载不可用。',
        'settings.tiktok.connected': '已连接 {url}',
        'settings.tiktok.unreachable': '无法连接 {url}，请确认服务已启动。',
        'settings.tiktok.bad_url': '服务地址需以 http:// 或 https:// 开头。',
        'settings.tiktok.saving': '正在保存…',
        'settings.tiktok.saved': 'TikTokDownloader 设置已保存',
        'settings.tiktok.save_failed': '保存失败，请稍后再试。',
        'settings.tiktok.dialog_copy': '按博主批量下载依赖局域网内的 TikTokDownloader Web API（默认端口 5555）。',
        'settings.tiktok.base_url_label': '服务地址 base_url',
        'settings.tiktok.token_label': '访问令牌 token（服务未启用鉴权时留空）',
        'settings.pairing.lan_warning': '该地址只能访问当前电脑，请使用手机可达的局域网 IP、.local 主机名或 HTTPS 地址。',
        'settings.pairing.incomplete': '请输入完整的 http:// 或 https:// 地址。',
        'settings.pairing.expires': '该单次链接将在 {time} 后过期。',
        'settings.pairing.expired': '链接已过期，请重新生成。',
        'settings.pairing.copied': '配对链接已复制',
        'settings.pairing.generating': '正在生成…',
        'settings.pairing.failed': '生成配对链接失败',
        'settings.douyin.enabled': '抖音模式已开启',
        'settings.douyin.disabled': '抖音模式已关闭',
        'settings.scan.confirm_title': '重新扫描媒体库？',
        'settings.scan.confirm_copy': '扫描将同步 TikLocal 运行期间新增、移动或删除的文件。',
        'settings.scan.action': '开始扫描',
        'settings.scan.busy': '正在扫描…',
        'settings.scan.done': '扫描完成，已索引 {count} 个媒体。',
        'settings.scan.failed': '扫描失败，请稍后再试。',
        'settings.scan.timeout': '扫描超时（大目录首次扫描可能超过 3 分钟），可稍后在设置页刷新查看结果。',
        'settings.scan.running': '已有扫描在进行中，请稍候再试。',
        'settings.scan.partial': '扫描完成，但有来源不可用：{sources}。请检查这些目录是否已挂载或仍存在。',
        'settings.scan.unavailable': '有来源不可用：{sources}。已索引其他来源的 {count} 个媒体。',
        'settings.cache.confirm_title': '清理缩略图缓存？',
        'settings.cache.confirm_copy': '原始媒体保留，缩略图会按需重新生成。',
        'settings.cache.action': '清理缓存',
        'settings.cache.busy': '正在清理…',
        'settings.cache.freed': '已释放 {size} MB',
        'settings.cache.failed': '清理失败，请稍后再试。',
        'settings.reset.confirm_title': '重置推荐历史？',
        'settings.reset.confirm_copy': '观看、跳过、完播与续播记录将被清空，收藏与合集不受影响。',
        'settings.reset.action': '重置历史',
        'settings.reset.busy': '正在重置…',
        'settings.reset.done': '推荐历史已重置',
        'settings.reset.failed': '重置失败，请稍后再试。',
        'settings.conc.set': '并发下载已设为 {n}',
        'settings.conc.failed': '保存下载设置失败',
        'settings.cred.select_file': '请先选择凭据文件',
        'settings.cred.uploading': '正在上传…',
        'settings.cred.updated': '凭据已更新',
        'settings.cred.upload_failed': '上传凭据失败',
        'settings.trash.summary_one': '共 {count} 项 · {size}；保留 {days} 天后删除。',
        'settings.trash.summary_other': '共 {count} 项 · {size}；保留 {days} 天后删除。',
        'settings.trash.retention': '已删除媒体将在此保留 {days} 天。',
        'settings.trash.days_left': '剩余 {days} 天',
        'settings.trash.days_left_one': '剩余 {days} 天',
        'settings.trash.restore': '恢复',
        'settings.trash.restoring': '正在恢复…',
        'settings.trash.restored': '已恢复 {name}',
        'settings.trash.open': '打开',
        'settings.trash.restore_failed': '恢复失败，请重试。',
        'settings.trash.delete_title': '永久删除 {name}？',
        'settings.trash.delete_copy': '文件将从磁盘删除，无法恢复。',
        'settings.trash.deleting': '正在删除…',
        'settings.trash.delete_failed': '删除失败，请重试。',
        'settings.trash.unavailable': '无法获取回收站状态。',
        'settings.trash.empty_title': '清空回收站？',
        'settings.trash.empty_copy': '回收站中的所有文件将从磁盘删除且无法恢复。',
        'settings.trash.empty_action': '清空回收站',
        'settings.trash.emptying': '正在清空…',
        'settings.trash.emptied': '已永久删除 {count} 项',
        'settings.trash.empty_partial': '已删除 {purged} 项，{failed} 项仍被占用，请关闭相关程序后重试。',
        'settings.trash.empty_failed': '清空失败，请稍后再试。',
        # ---- settings page copy (templates) ----
        'settings.overline': '本地偏好',
        'settings.intro': '管理外观、媒体索引与本地服务。',
        'settings.theme.title': '显示模式',
        'settings.theme.copy': '选择后立即应用到所有页面。',
        'settings.theme.group': '显示模式',
        'settings.radio.pair_title': '配对新手机',
        'settings.radio.pair_copy': '生成两分钟内有效的单次配对链接。',
        'settings.radio.generate': '生成',
        'settings.radio.paired_title': '已配对设备',
        'settings.radio.loading': '正在加载设备令牌…',
        'settings.library.items_unit': '个本地媒体',
        'settings.library.videos': '个视频',
        'settings.library.images': '张图片',
        'settings.library.loading': '正在加载索引状态…',
        'settings.library.rescan_title': '重新扫描媒体库',
        'settings.library.rescan_copy': 'TikLocal 运行期间外部增删改文件后使用。',
        'settings.library.scan': '扫描',
        'settings.library.cache_title': '缩略图缓存',
        'settings.library.cache_copy': '当前 {size}；缩略图会在浏览时按需重新生成。',
        'settings.library.clear': '清理',
        'settings.library.sources_title': '媒体目录',
        'settings.library.sources_copy': '可以添加多个文件夹，保存后立即重新扫描。',
        'settings.library.sources_add': '添加目录',
        'settings.library.sources_loading': '正在读取媒体目录…',
        'settings.library.sources_empty': '还没有媒体目录，先添加一个文件夹。',
        'settings.library.source_default': '默认',
        'settings.library.source_items': '{count} 个媒体',
        'settings.library.source_unavailable': '无法访问',
        'settings.library.source_edit': '编辑',
        'settings.library.source_remove': '移除',
        'settings.library.source_add_title': '添加媒体目录',
        'settings.library.source_edit_title': '编辑媒体目录',
        'settings.library.source_dialog_copy': '保存后立即生效，并会写入本机实例目录，重启后依然有效。',
        'settings.library.source_path_label': '文件夹路径',
        'settings.library.source_path_placeholder': r'例如 D:\Videos',
        'settings.library.source_name_label': '名称（可选）',
        'settings.library.source_saving': '保存中…',
        'settings.library.source_remove_title': '移除媒体目录',
        'settings.library.source_remove_copy': '将从媒体库移除「{name}」，磁盘上的文件不会被删除。',
        'settings.library.source_remove_action': '移除',
        'settings.library.source_added': '已添加「{name}」',
        'settings.library.source_updated': '已更新「{name}」',
        'settings.library.source_removed': '媒体目录已移除',
        'settings.library.source_trash_not_empty': '这个目录的回收站里还有文件，请先清空回收站再移除目录。',
        'settings.library.source_scan_pending': '已保存；媒体库正在扫描，稍后会显示这些文件。',
        'settings.library.source_scan_done': '已重新扫描，共 {count} 个媒体。',
        'settings.library.source_scan_unavailable': '这些文件夹这次没能扫描到：{names}。请确认目录已挂载或仍然存在，然后重新扫描。',
        'settings.library.source_scan_failed': '文件夹已保存，但扫描失败（磁盘或索引不可写），这些文件暂时不会出现在媒体库里。',
        'settings.library.source_error_generic': '媒体目录操作失败，请稍后重试。',
        'settings.library.source_timeout': '扫描超时，文件夹可能已保存；稍后刷新本页查看结果。',
        'settings.library.source_error_save': '保存媒体目录失败，请检查磁盘写入权限。',
        'settings.library.source_error_required': '请填写文件夹路径。',
        'settings.library.source_error_missing': '找不到这个文件夹：{path}',
        'settings.library.source_error_not_dir': '这不是文件夹：{path}',
        'settings.library.source_error_data_dir': '这是 TikLocal 自己的数据目录，里面存着密码与密钥，不能当作媒体目录。',
        'settings.library.source_error_unreadable': '没有读取权限：{path}',
        'settings.library.source_error_duplicate': '该文件夹已经在媒体目录列表中。',
        'settings.library.source_error_too_many': '媒体目录最多 {max} 个。',
        'settings.library.source_error_last': '至少要保留一个媒体目录。',
        'settings.library.source_error_default': '默认目录不能移除，可以编辑它的路径。',
        'settings.library.source_error_download_target': '下载目录不能移除，请先把下载目录换到别处。',
        'settings.library.source_download_active': '还有下载任务在运行，请等它们结束再更换下载目录。',
        'settings.library.source_error_not_found': '这个媒体目录已经不存在了。',
        'settings.library.source_error_store': '实例目录不可写，无法保存媒体目录。',
        'settings.trash.title': '回收站',
        'settings.trash.empty': '清空',
        'settings.privacy.title': '隐私与推荐',
        'settings.privacy.history_title': '本地浏览历史',
        'settings.privacy.history_copy': '重置观看、跳过、完播与续播记录，不影响收藏与合集。',
        'settings.privacy.reset': '重置',
        'settings.password.title': '修改访问密码',
        'settings.password.copy': '定期更换更安全；改完后其他手机和浏览器需要重新登录。',
        'settings.password.action': '修改',
        'settings.password.dialog_copy': '先输入当前密码，再设置至少 {n} 位的新密码。',
        'settings.password.current_label': '当前密码',
        'settings.password.new_label': '新密码',
        'settings.password.confirm_label': '确认新密码',
        'settings.password.required': '请填写当前密码和新密码。',
        'settings.password.too_short': '新密码至少需要 {n} 位。',
        'settings.password.mismatch': '两次输入的新密码不一致。',
        'settings.password.same': '新密码不能与当前密码相同。',
        'settings.password.wrong_current': '当前密码不正确。',
        'settings.password.saving': '正在保存…',
        'settings.password.done': '访问密码已更新，其他设备需要重新登录。',
        'settings.password.failed': '修改失败，请稍后再试。',
        'settings.password.disabled': '当前没有开启访问密码。',
        'settings.download.components_title': '下载组件',
        'settings.download.checking': '正在检查状态…',
        'settings.download.concurrent_title': '并发下载数',
        'settings.download.concurrent_copy': '控制并行任务数，大多数设备 2 即可。',
        'settings.download.concurrent_custom': '当前为自定义并发数（{n}）。',
        'settings.download.concurrency_aria': '并发下载任务',
        'settings.download.tiktok_title': 'TikTokDownloader 服务',
        'settings.download.edit': '编辑',
        'settings.download.credentials_title': '网站凭据',
        'settings.download.manage': '管理',
        'settings.experiments.title': '实验功能',
        'settings.experiments.similarity_title': '相似图片',
        'settings.experiments.similarity_copy': '查看本地向量状态与相似分组。',
        'settings.about.contact': '联系作者',
        'settings.cred.copy': '仅用于需要登录的内容，文件仍保存在本机。',
        'settings.cred.loading': '正在加载凭据…',
        'settings.cred.add_title': '添加或更新',
        'settings.cred.add_copy': '支持 .txt 与 .cookies 文件，同名上传将覆盖。',
        'settings.cred.upload': '上传',
        'settings.pairing.title': '配对 TikLocal Radio',
        'settings.pairing.copy': '链接不含访问密码或长期设备令牌，两分钟后过期。',
        'settings.pairing.server_label': '手机可访问的服务地址',
        'settings.pairing.paste_label': '粘贴到 TikLocal Radio',
        'settings.pairing.generate': '生成配对链接',
        'settings.pairing.copy_link': '复制链接',
        'settings.pairing.open_here': '在本设备打开',
        'settings.pairing.qr_alt': 'TikLocal Radio 单次配对二维码',
    },
    'en': {
        # ---- common (base.html, shared JS) ----
        'common.app_name': 'tikLocal',
        'common.unknown_time': 'Unknown time',
        'common.cancel': 'Cancel',
        'common.confirm': 'Confirm',
        'common.close': 'Close',
        'common.delete': 'Delete',
        'common.save': 'Save',
        'common.loading': 'Loading…',
        'common.retry': 'Retry',
        'common.error.not_found': 'The requested resource does not exist.',
        'common.error.method_not_allowed': 'This request method is not supported.',
        'common.error.internal': 'The server failed to handle the request. Please try again.',
        'common.error.storage_unavailable': 'Storage is not writable. Check free space and permissions, then retry.',
        'common.error.offline': 'Cannot reach the server. Check the network or make sure TikLocal is still running.',
        'common.error.timeout': 'The server took too long to answer. Try again shortly.',
        'common.error.too_large': 'The upload exceeds the {limit} limit. Shrink it and retry.',
        'download.error.file_move_failed': 'The file could not be moved to the trash; another program may be using it.',
        'download.error.canceled': 'Canceled.',
        'download.error.no_output': 'The download finished but no file reached the library.',
        'download.error.interrupted_restart': 'The job was interrupted by a service restart.',
        'download.error.os_failure': 'A file operation failed ({reason}, errno {errno}). Check free space and whether another program holds the file, then retry.',
        'download.probe_failed': 'Could not check the download tools; engines may be unavailable for now.',
        'common.all': 'All',
        'common.search': 'Search',
        'common.open_settings': 'Open Settings',
        # ---- navigation ----
        'nav.home': 'Home',
        'nav.flow': 'Play',
        'nav.radio': 'Radio',
        'nav.library': 'Library',
        'nav.saved': 'Saved',
        'nav.download': 'Download',
        'nav.settings': 'Settings',
        'nav.group.overview': 'Overview',
        'nav.group.experience': 'Experience',
        'nav.group.media': 'Media',
        'nav.sign_out': 'Sign Out',
        'nav.open_menu': 'Open media and tools menu',
        'nav.return_home': 'Return home',
        'nav.experience_mode': 'Experience mode',
        'nav.app_navigation': 'App navigation',
        'nav.skip_to_content': 'Skip to content',
        'nav.main_navigation': 'Main navigation',
        'nav.media_and_tools': 'Media and tools',
        'nav.tiklocal_home': 'TikLocal home',
        'nav.sign_out_label': 'Sign out',
        # ---- page titles ----
        'title.download': 'Media Import',
        'title.saved': 'Saved',
        'title.settings': 'Settings',
        'title.login': 'Sign in',
        # ---- settings page ----
        'settings.section.appearance': 'Appearance',
        'settings.section.radio_clients': 'Radio Clients',
        'settings.section.library': 'Media Library',
        'settings.section.trash': 'Recently Deleted',
        'settings.section.privacy': 'Privacy',
        'settings.section.security': 'Access Password',
        'settings.section.downloads': 'Downloads',
        'settings.section.about': 'About',
        'settings.theme.light': 'Light',
        'settings.theme.dark': 'Dark',
        'settings.theme.system': 'System',
        'settings.douyin.title': 'Douyin Feed Style',
        'settings.douyin.copy': 'Show the Flow feed with Douyin-style navigation, author, and caption overlays.',
        'settings.language.title': 'Language',
        'settings.language.copy': 'Applies immediately.',
        'settings.language.zh': '中文',
        'settings.language.en': 'English',
        'settings.language.changed': 'Language updated',
        'settings.language.cookie_blocked': 'Your browser blocked the cookie, so the language did not change. Allow cookies for this site and try again.',
        # ---- settings page messages (JS) ----
        'settings.request_failed': 'Request failed',
        'settings.sync.no_scan': 'No scan completed yet',
        'settings.sync.recent': 'A scan completed recently',
        'settings.sync.last': 'Last scanned: {time}',
        'settings.sync.unavailable': 'Index status is unavailable',
        'settings.device.pair_unknown': 'Pairing time unknown',
        'settings.device.paired': 'Paired {time}',
        'settings.device.none_title': 'No Radio clients yet',
        'settings.device.none_copy': 'Enter this server address and access password in TikLocal Radio to pair.',
        'settings.device.token_active': 'Token active',
        'settings.device.invalidated': 'Invalidated by password change',
        'settings.device.revoke': 'Revoke',
        'settings.device.revoke_title': 'Revoke this Radio client?',
        'settings.device.revoke_copy': '{name} will lose media access and must pair again with the access password.',
        'settings.device.revoke_action': 'Revoke Client',
        'settings.device.revoking': 'Revoking…',
        'settings.device.revoked_toast': 'Radio client revoked',
        'settings.device.revoke_failed': 'Failed to revoke the client. Please try again later.',
        'settings.device.unavailable': 'Paired devices are unavailable.',
        'settings.device.this_device': 'This device',
        'settings.cred.saved_summary': '{count} credential files saved and matched to websites automatically.',
        'settings.cred.none_summary': 'None added. Public content can still be downloaded.',
        'settings.cred.none': 'No credentials saved',
        'settings.dl.part_video': 'video',
        'settings.dl.part_image': 'image',
        'settings.dl.part_merge': 'media merging',
        'settings.dl.not_ready': '{parts} components are not ready.',
        'settings.dl.ready': 'Image and video downloads are ready.',
        'settings.dl.status_unavailable': 'Download status is unavailable.',
        'settings.dl.cred_unavailable': 'Credentials are unavailable.',
        'settings.tiktok.not_configured': 'No service address configured; blogger batch downloads are unavailable.',
        'settings.tiktok.connected': 'Connected {url}',
        'settings.tiktok.unreachable': 'Cannot reach {url}; make sure the service is running.',
        'settings.tiktok.bad_url': 'Server address must start with http:// or https://.',
        'settings.tiktok.saving': 'Saving…',
        'settings.tiktok.saved': 'TikTokDownloader settings saved',
        'settings.tiktok.save_failed': 'Save failed. Please try again later.',
        'settings.tiktok.dialog_copy': 'Blogger batch downloads rely on a LAN TikTokDownloader Web API (default port 5555).',
        'settings.tiktok.base_url_label': 'Server address base_url',
        'settings.tiktok.token_label': 'Access token (leave empty when the service has no auth)',
        'settings.pairing.lan_warning': 'This address only reaches the current computer. Use a LAN IP, .local hostname, or HTTPS address your phone can reach.',
        'settings.pairing.incomplete': 'Enter a complete http:// or https:// address.',
        'settings.pairing.expires': 'This single-use link expires in {time}.',
        'settings.pairing.expired': 'This link has expired. Generate a new one.',
        'settings.pairing.copied': 'Pairing link copied',
        'settings.pairing.generating': 'Generating…',
        'settings.pairing.failed': 'Failed to generate pairing link',
        'settings.douyin.enabled': 'Douyin feed style enabled',
        'settings.douyin.disabled': 'Douyin feed style disabled',
        'settings.scan.confirm_title': 'Rescan the media library?',
        'settings.scan.confirm_copy': 'The scan will reconcile files added, moved, or deleted while TikLocal was running.',
        'settings.scan.action': 'Start Scan',
        'settings.scan.busy': 'Scanning…',
        'settings.scan.done': 'Scan complete. Indexed {count} media items.',
        'settings.scan.failed': 'Scan failed. Please try again later.',
        'settings.scan.timeout': 'The scan timed out (a first scan of a large folder can take minutes); reload this page later to see the result.',
        'settings.scan.running': 'A scan is already running. Please try again shortly.',
        'settings.scan.partial': 'Scan finished, but some sources are unavailable: {sources}. Check that those directories are mounted and still exist.',
        'settings.scan.unavailable': 'Some sources are unavailable: {sources}. Indexed {count} media items from the others.',
        'settings.cache.confirm_title': 'Clear the thumbnail cache?',
        'settings.cache.confirm_copy': 'Original media will be kept. Thumbnails regenerate as needed while browsing.',
        'settings.cache.action': 'Clear Cache',
        'settings.cache.busy': 'Clearing…',
        'settings.cache.freed': 'Freed {size} MB',
        'settings.cache.failed': 'Cleanup failed. Please try again later.',
        'settings.reset.confirm_title': 'Reset recommendation history?',
        'settings.reset.confirm_copy': 'Views, skips, completions, and resume points will be cleared. Favorites and collections remain unchanged.',
        'settings.reset.action': 'Reset History',
        'settings.reset.busy': 'Resetting…',
        'settings.reset.done': 'Recommendation history reset',
        'settings.reset.failed': 'Reset failed. Please try again later.',
        'settings.conc.set': 'Concurrent downloads set to {n}',
        'settings.conc.failed': 'Failed to save download settings',
        'settings.cred.select_file': 'Select a credential file first',
        'settings.cred.uploading': 'Uploading…',
        'settings.cred.updated': 'Credentials updated',
        'settings.cred.upload_failed': 'Failed to upload credentials',
        'settings.trash.summary_one': '{count} item · {size}. Kept for {days} days, then removed.',
        'settings.trash.summary_other': '{count} items · {size}. Kept for {days} days, then removed.',
        'settings.trash.retention': 'Deleted media stays here for {days} days.',
        'settings.trash.days_left': '{days} days left',
        'settings.trash.days_left_one': '{days} day left',
        'settings.trash.restore': 'Restore',
        'settings.trash.restoring': 'Restoring…',
        'settings.trash.restored': 'Restored {name}',
        'settings.trash.open': 'Open',
        'settings.trash.restore_failed': 'Restore failed. Please try again.',
        'settings.trash.delete_title': 'Delete {name} permanently?',
        'settings.trash.delete_copy': 'The file is removed from disk and cannot be restored.',
        'settings.trash.deleting': 'Deleting…',
        'settings.trash.delete_failed': 'Delete failed. Please try again.',
        'settings.trash.unavailable': 'Trash is unavailable.',
        'settings.trash.empty_title': 'Empty the trash?',
        'settings.trash.empty_copy': 'Every file in the trash is removed from disk and cannot be restored.',
        'settings.trash.empty_action': 'Empty Trash',
        'settings.trash.emptying': 'Emptying…',
        'settings.trash.emptied': 'Deleted {count} items permanently',
        'settings.trash.empty_partial': 'Deleted {purged} items; {failed} are still in use. Close them and try again.',
        'settings.trash.empty_failed': 'Failed to empty the trash. Please try again.',
        # ---- settings page copy (templates) ----
        'settings.overline': 'Local Preferences',
        'settings.intro': 'Manage appearance, the media index, and local services.',
        'settings.theme.title': 'Display Mode',
        'settings.theme.copy': 'Your selection applies to every page immediately.',
        'settings.theme.group': 'Display mode',
        'settings.radio.pair_title': 'Pair a New Phone',
        'settings.radio.pair_copy': 'Generate a secure, single-use pairing link that expires in two minutes.',
        'settings.radio.generate': 'Generate',
        'settings.radio.paired_title': 'Paired Devices',
        'settings.radio.loading': 'Loading device tokens…',
        'settings.library.items_unit': 'local media items',
        'settings.library.videos': 'videos',
        'settings.library.images': 'images',
        'settings.library.loading': 'Loading index status…',
        'settings.library.rescan_title': 'Rescan Media Library',
        'settings.library.rescan_copy': 'Use after files are added, moved, or deleted outside TikLocal while it is running.',
        'settings.library.scan': 'Scan',
        'settings.library.cache_title': 'Thumbnail Cache',
        'settings.library.cache_copy': 'Currently {size}; thumbnails regenerate as needed while browsing.',
        'settings.library.clear': 'Clear',
        'settings.library.sources_title': 'Media Folders',
        'settings.library.sources_copy': 'Add as many folders as you like; saving rescans the library.',
        'settings.library.sources_add': 'Add folder',
        'settings.library.sources_loading': 'Loading media folders…',
        'settings.library.sources_empty': 'No media folders yet. Add a folder to get started.',
        'settings.library.source_default': 'Default',
        'settings.library.source_items': '{count} items',
        'settings.library.source_unavailable': 'Unavailable',
        'settings.library.source_edit': 'Edit',
        'settings.library.source_remove': 'Remove',
        'settings.library.source_add_title': 'Add Media Folder',
        'settings.library.source_edit_title': 'Edit Media Folder',
        'settings.library.source_dialog_copy': 'Applies immediately and is stored with this instance, so it survives a restart.',
        'settings.library.source_path_label': 'Folder path',
        'settings.library.source_path_placeholder': r'e.g. D:\Videos',
        'settings.library.source_name_label': 'Name (optional)',
        'settings.library.source_saving': 'Saving…',
        'settings.library.source_remove_title': 'Remove Media Folder',
        'settings.library.source_remove_copy': '“{name}” is removed from the library; the files on disk are kept.',
        'settings.library.source_remove_action': 'Remove',
        'settings.library.source_added': 'Added “{name}”',
        'settings.library.source_updated': 'Updated “{name}”',
        'settings.library.source_removed': 'Media folder removed',
        'settings.library.source_trash_not_empty': 'This folder still has files in its trash; empty the trash before removing it.',
        'settings.library.source_scan_pending': 'Saved; the library is scanning and these files will show up shortly.',
        'settings.library.source_scan_done': 'Rescanned {count} items.',
        'settings.library.source_scan_unavailable': 'These folders could not be scanned: {names}. Make sure each folder is still mounted or reachable, then rescan.',
        'settings.library.source_scan_failed': 'The folder was saved, but the scan failed (disk or index not writable); these files will not appear in the library yet.',
        'settings.library.source_error_generic': 'Could not update the media folders. Try again.',
        'settings.library.source_timeout': 'The scan timed out; the folder may already be saved - reload this page in a moment.',
        'settings.library.source_error_save': 'Could not save the media folders; check the disk write permissions.',
        'settings.library.source_error_required': 'Enter a folder path.',
        'settings.library.source_error_missing': 'That folder does not exist: {path}',
        'settings.library.source_error_not_dir': 'That is not a folder: {path}',
        'settings.library.source_error_data_dir': 'That is TikLocal\'s own data folder; it holds the password and keys and cannot be a media folder.',
        'settings.library.source_error_unreadable': 'That folder cannot be read: {path}',
        'settings.library.source_error_duplicate': 'That folder is already in the list.',
        'settings.library.source_error_too_many': 'At most {max} media folders.',
        'settings.library.source_error_last': 'Keep at least one media folder.',
        'settings.library.source_error_default': 'The default folder cannot be removed; edit its path instead.',
        'settings.library.source_error_download_target': 'This folder receives downloads; move downloads elsewhere first.',
        'settings.library.source_download_active': 'Downloads are still running; wait for them to finish before changing the download folder.',
        'settings.library.source_error_not_found': 'That media folder no longer exists.',
        'settings.library.source_error_store': 'The instance directory is not writable, so folders cannot be saved.',
        'settings.trash.title': 'Trash',
        'settings.trash.empty': 'Empty',
        'settings.privacy.title': 'Privacy and Recommendations',
        'settings.privacy.history_title': 'Local Browsing History',
        'settings.privacy.history_copy': 'Reset views, skips, completions, and resume points without changing favorites or collections.',
        'settings.privacy.reset': 'Reset',
        'settings.password.title': 'Change Access Password',
        'settings.password.copy': 'Rotate it now and then; other phones and browsers will need to sign in again.',
        'settings.password.action': 'Change',
        'settings.password.dialog_copy': 'Enter the current password, then choose a new one with at least {n} characters.',
        'settings.password.current_label': 'Current password',
        'settings.password.new_label': 'New password',
        'settings.password.confirm_label': 'Confirm new password',
        'settings.password.required': 'Enter the current password and a new one.',
        'settings.password.too_short': 'The new password needs at least {n} characters.',
        'settings.password.mismatch': 'The two new passwords do not match.',
        'settings.password.same': 'The new password must differ from the current one.',
        'settings.password.wrong_current': 'The current password is not correct.',
        'settings.password.saving': 'Saving…',
        'settings.password.done': 'Access password updated. Other devices must sign in again.',
        'settings.password.failed': 'Could not change the password. Please try again later.',
        'settings.password.disabled': 'The access password is turned off.',
        'settings.download.components_title': 'Download Components',
        'settings.download.checking': 'Checking status…',
        'settings.download.concurrent_title': 'Concurrent Downloads',
        'settings.download.concurrent_copy': 'Control parallel jobs. Two works well for most devices.',
        'settings.download.concurrent_custom': 'Using a custom concurrency of {n}.',
        'settings.download.concurrency_aria': 'Concurrent download jobs',
        'settings.download.tiktok_title': 'TikTokDownloader service',
        'settings.download.edit': 'Edit',
        'settings.download.credentials_title': 'Website Credentials',
        'settings.download.manage': 'Manage',
        'settings.experiments.title': 'Experiments',
        'settings.experiments.similarity_title': 'Similar Images',
        'settings.experiments.similarity_copy': 'View local vector status and similarity groups.',
        'settings.about.contact': 'Contact the Author',
        'settings.cred.copy': 'Use them for sign-in-only content. Files remain on this device.',
        'settings.cred.loading': 'Loading credentials…',
        'settings.cred.add_title': 'Add or Update',
        'settings.cred.add_copy': 'Supports .txt and .cookies files. Uploading the same filename replaces it.',
        'settings.cred.upload': 'Upload',
        'settings.pairing.title': 'Pair TikLocal Radio',
        'settings.pairing.copy': 'The link contains no access password or long-lived device token and expires after two minutes.',
        'settings.pairing.server_label': 'Server address reachable from your phone',
        'settings.pairing.paste_label': 'Paste into TikLocal Radio',
        'settings.pairing.generate': 'Generate Pairing Link',
        'settings.pairing.copy_link': 'Copy Link',
        'settings.pairing.open_here': 'Open on This Device',
        'settings.pairing.qr_alt': 'Single-use TikLocal Radio pairing QR code',
    },
}

_JS_REVEAL = ('window.__TIKLOCAL_I18N__ = ', ';\n')


def _merge_page_catalogs() -> None:
    """Fold the per-page catalogs into ``TRANSLATIONS``.

    Each page domain lives in its own ``tiklocal/i18n_pages_*.py`` module so two
    page conversions never edit the same file. A key defined twice is a bug:
    the merge raises instead of silently shadowing one of them.
    """
    from tiklocal import (
        i18n_pages_chrome,
        i18n_pages_flow,
        i18n_pages_library,
        i18n_pages_media,
        i18n_pages_radio,
        i18n_pages_search,
        i18n_pages_similarity,
        i18n_pages_sync,
        i18n_pages_ui,
    )

    for module in (
        i18n_pages_chrome,
        i18n_pages_flow,
        i18n_pages_library,
        i18n_pages_media,
        i18n_pages_radio,
        i18n_pages_search,
        i18n_pages_similarity,
        i18n_pages_sync,
        i18n_pages_ui,
    ):
        table = getattr(module, 'TRANSLATIONS', {})
        for locale in SUPPORTED_LOCALES:
            for key, text in (table.get(locale) or {}).items():
                if key in TRANSLATIONS[locale]:
                    raise ValueError(f'duplicate i18n key {key!r} in {module.__name__}')
                TRANSLATIONS[locale][key] = text


_merge_page_catalogs()


def normalize_locale(value: str | None) -> str:
    """Map an arbitrary language tag to a supported locale."""
    if not value:
        return DEFAULT_LOCALE
    tag = value.strip().lower()
    if tag.startswith(SUPPORTED_LOCALES[0]):
        return SUPPORTED_LOCALES[0]
    if tag.startswith(SUPPORTED_LOCALES[1]):
        return SUPPORTED_LOCALES[1]
    return DEFAULT_LOCALE


def detect_locale(get_cookie, accept_language: str | None) -> str:
    """Cookie choice wins, then Accept-Language quality order, then default."""
    cookie_value = get_cookie(LOCALE_COOKIE)
    if cookie_value:
        return normalize_locale(cookie_value)
    candidates: list[tuple[float, str, bool]] = []
    for match in _LANG_RE.finditer(accept_language or ''):
        tag, quality = match.group(1), match.group(2)
        try:
            q = float(quality) if quality is not None else 1.0
        except ValueError:
            q = 0.0
        prefix = tag.strip().lower()
        explicit = prefix.startswith(SUPPORTED_LOCALES) and any(
            prefix == locale or prefix.startswith(locale + '-')
            for locale in SUPPORTED_LOCALES
        )
        candidates.append((q, normalize_locale(tag), explicit))
    # Prefer the highest-quality tag the user explicitly asked for; an
    # unsupported language (ja, fr, …) falls through to the default. A tag sent
    # with q=0 is a refusal, not a preference: "zh-CN;q=0, en;q=0.9" asks for
    # English, and returning Chinese ignored the one thing the header said.
    for q, locale, explicit in sorted(candidates, key=lambda row: -row[0]):
        if explicit and q > 0:
            return locale
    return DEFAULT_LOCALE


def translate(locale: str, key: str, **params: object) -> str:
    table = TRANSLATIONS.get(locale) or TRANSLATIONS[DEFAULT_LOCALE]
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS[DEFAULT_LOCALE].get(key, key)
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def js_catalog(locale: str) -> dict[str, str]:
    """Full table for the active locale; client scripts share the same keys.

    Missing keys fall back to the default locale, the same way translate() does.
    Shipping only the active table meant a string that exists in one language
    only rendered as its raw key in the browser while the server-rendered page
    showed the fallback text.
    """
    merged = dict(TRANSLATIONS[DEFAULT_LOCALE])
    merged.update(TRANSLATIONS.get(locale) or {})
    return merged


def js_catalog_script(locale: str) -> str:
    import json

    return _JS_REVEAL[0] + json.dumps(js_catalog(locale), ensure_ascii=False) + _JS_REVEAL[1]


def current_locale() -> str:
    """Locale of the active request, or the default outside one."""
    try:
        from flask import has_request_context, request
    except ImportError:  # pragma: no cover - Flask is a hard dependency
        return DEFAULT_LOCALE
    if not has_request_context():
        return DEFAULT_LOCALE
    return detect_locale(request.cookies.get, request.headers.get('Accept-Language'))


def t(key: str, **params: object) -> str:
    """Translate a key for the active request (for Python-side messages)."""
    return translate(current_locale(), key, **params)
