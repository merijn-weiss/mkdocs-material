import urllib.parse

def define_env(env):
    """
    This is the hook for the functions triggered by the https://mkdocs-macros-plugin.readthedocs.io/ plugin.
    """

    @env.macro
    def encodeURL(url):
        return urllib.parse.quote(url, safe='')

    @env.macro
    def createMondrianViewURL(url, title=None):
        mondrianViewURL = env.variables['mondrianDiagramsViewer']
        if(title==None):
            title=url
        preFix = "javascript:"
        functionCall = "openMondrianDiagramViewer(\"" + mondrianViewURL + "\",\"" + url + "\");"
        return '[' + title + ']' + '(' + preFix + urllib.parse.quote(functionCall, safe='') + ')'
