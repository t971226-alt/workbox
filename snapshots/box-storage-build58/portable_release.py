"""Every distributed program change gets a strictly increasing release identity."""
VERSION = '1.1.0'
RELEASE_BUILD = 58
RELEASE_KEY = (1, 1, 0, RELEASE_BUILD)
RELEASE_LABEL = VERSION + ' · 第 ' + str(RELEASE_BUILD) + ' 次构建'
APP_ID = 'workbox.portable'
