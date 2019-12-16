# coding:utf-8

from burp import IBurpExtender
from burp import IMessageEditorTabFactory
from burp import IMessageEditorTab
from burp import IHttpListener
from burp import IContextMenuFactory
from burp import IContextMenuInvocation
from burp import IBurpExtenderCallbacks
from burp import ITab

from javax.swing import JFrame, JPanel, JButton, JLabel, JTextArea, JTextField, JMenuItem
from javax.swing import JScrollPane, JTabbedPane
from javax.swing import JCheckBox, JComboBox
from javax.swing import Box, BorderFactory
from javax.swing import SwingConstants
from javax.swing import BoxLayout
from javax.swing.undo import UndoManager, CompoundEdit
from javax.swing.event import UndoableEditEvent, DocumentListener
from javax.swing.text import PlainDocument
from java.awt.event import ItemEvent, FocusListener, MouseAdapter
from java.awt import GridBagLayout, GridBagConstraints, BorderLayout, FlowLayout
from java.awt import Dimension, Color, Component

import re
import json
from threading import Thread
import xml.dom.minidom

import jsbeautifier
from bs4 import BeautifulSoup


# "xxContentTypes" values below should be lower-case
jsContentTypes = [
    "application/javascript",
    "application/x-javascript",
    "text/javascript"
]
jsonContentTypes = [
    "application/json",
    "text/json",
    "text/x-json"
]
htmlContentTypes = [
    "text/html"
]
xmlContentTypes = [
    "text/xml",
    "application/xml",
    "image/svg+xml"
]

# formats (string constants)
F_JS = "JavaScript"
F_JSON = "JSON"
F_HTML = "HTML"
F_XML = "XML"

supportedFormats = [F_JS, F_JSON, F_HTML, F_XML]

options = {
    "general": {
        "dataMaxSize": 680 * 1024  # Data size limit, cause the Jython is slow. 680 KB by default
    },
    "messageEditorTabFormat": {
        F_JS: True,
        F_JSON: True,
        F_HTML: False,
        F_XML: False,
    },
    "replaceProxyResponse": {
        "enable": False,
        "formats": {
            F_JS: True,
            F_JSON: False,
            F_HTML: False,
            F_XML: False,
        },
        "include": [],
        "exclude": []
    }
}

def contentType2Format(contentType):
    contentType = contentType.lower()  # lower first
    format = None
    if any(map(lambda c: contentType.find(c) >= 0, jsContentTypes)):
        format = F_JS
    if any(map(lambda c: contentType.find(c) >= 0, jsonContentTypes)):
        format = F_JSON
    if any(map(lambda c: contentType.find(c) >= 0, htmlContentTypes)):
        format = F_HTML
    if any(map(lambda c: contentType.find(c) >= 0, xmlContentTypes)):
        format = F_XML
    return format

def mimeType2Format(mimeType):
    format = None
    mimeType = mimeType.upper()
    if mimeType == "JSON":
        format = F_JSON
    elif mimeType == "SCRIPT":
        format = F_JS
    elif mimeType == "XML":
        format = F_XML
    elif mimeType == "HTML":
        format = F_HTML
    return format

class BeautifyException(Exception):
    pass

def beautify(data, format, raise_exception=False, beautify_html=True):
    """
    Main beautify function
    :param data: if data is <str> type, it should be encoded in utf8
    :type data: str or unicode
    :param format:
    :param raise_exception:
    :param beautify_html: beautify html if True, otherwise just js code in html
    :return:
    :rtype: return <str> if data is <str> type; return <unicode> if data is <unicode> type. The error msg is always <str> type
    """
    if len(data) > options.get("general").get("dataMaxSize"):
        result = "Max Size Limit"
        if raise_exception:
            raise BeautifyException(result)
        return result
    # print("data", type(data), data.__repr__())
    try:
        if format == F_JS:
            # if data is <str> type, result is <str> too; if data is <unicode> type, result is <unicode> too
            result = jsbeautifier.beautify(data)
        elif format == F_JSON:
            opts = jsbeautifier.default_options()
            opts.preserve_newlines = False
            result = jsbeautifier.beautify(data, opts)
        elif format == F_HTML:
            soup = BeautifulSoup(data, features="html.parser")
            # beautify jscode in html
            scriptElements = soup.find_all("script")
            for e in scriptElements:
                if e.attrs.get("type", "").lower() == "text/template":
                    continue
                if len(e.text) > 0:
                    e.string = jsbeautifier.beautify(e.text)
            if beautify_html:
                if isinstance(data, str):
                    result = soup.prettify("utf8")
                else:
                    result = soup.prettify()
            else:
                if isinstance(data, str):
                    result = str(soup)
                else:
                    result = unicode(soup)
        elif format == F_XML:
            data = "".join([line.strip() for line in data.splitlines()])
            if isinstance(data, str):
                dom = xml.dom.minidom.parseString(data)
            else:
                dom = xml.dom.minidom.parseString(data.encode("utf8"))
            if isinstance(data, str):
                result = dom.toprettyxml(indent="  ", newl="\n", encoding="utf8")
            else:
                result = dom.toprettyxml(indent="  ", newl="\n")
        else:
            result = "Not support %s" % format
            if raise_exception:
                raise BeautifyException(result)
    except Exception as e:
        result = "Error: %s" % e.__repr__()
        if raise_exception:
            raise BeautifyException(result)
    # print("result", type(result), result.__repr__())
    return result


class BeautifyThread(Thread):
    def __init__(self, data, format, callback):
        super(BeautifyThread, self).__init__()
        self.data = data
        self.format = format
        self.callback = callback

    def run(self):
        result = beautify(self.data, self.format)
        if self.callback:
            self.callback(result)


class BurpExtender(IBurpExtender, ITab, IMessageEditorTabFactory, IHttpListener, IContextMenuFactory):
    # implement IBurpExtender
    def	registerExtenderCallbacks(self, callbacks):
        self._callbacks = callbacks
        self._helpers = callbacks.getHelpers()

        callbacks.setExtensionName("Beautifier")

        self.loadOptions()

        self.mainTabbedPane = JTabbedPane()
        self.beautifierPanel = BeautifierPanel()
        self.beautifierOptionsPanel = BeautifierOptionsPanel(self)
        self.mainTabbedPane.addTab("Beautify", self.beautifierPanel)
        self.mainTabbedPane.addTab("Options", self.beautifierOptionsPanel)

        self.selectedMessage = None

        callbacks.addSuiteTab(self)
        callbacks.registerMessageEditorTabFactory(self)
        callbacks.registerHttpListener(self)
        callbacks.registerContextMenuFactory(self)

    # implement IMessageEditorTabFactory
    def createNewInstance(self, controller, editable):
        return BeautifierTab(self, controller, editable)

    # implement IContextMenuFactory
    def createMenuItems(self, invocation):
        iContext = invocation.getInvocationContext()
        self.selectedMessage = invocation.getSelectedMessages()[0]

        menuItems = []
        sendRequestMenu = JMenuItem("Send to Beautifier (request)")
        sendRequestMenu.addActionListener(self.sendRequestToBeautifier)
        sendResponseMenu = JMenuItem("Send to Beautifier (response)")
        sendResponseMenu.addActionListener(self.sendResponseToBeautifier)

        if iContext == IContextMenuInvocation.CONTEXT_MESSAGE_EDITOR_REQUEST or \
            iContext == IContextMenuInvocation.CONTEXT_MESSAGE_VIEWER_REQUEST:
            menuItems.append(sendRequestMenu)
        elif iContext == IContextMenuInvocation.CONTEXT_MESSAGE_EDITOR_RESPONSE or \
            iContext == IContextMenuInvocation.CONTEXT_MESSAGE_VIEWER_RESPONSE:
            menuItems.append(sendResponseMenu)
        elif iContext == IContextMenuInvocation.CONTEXT_PROXY_HISTORY:
            menuItems.append(sendRequestMenu)
            menuItems.append(sendResponseMenu)

        return menuItems

    def sendRequestToBeautifier(self, e):
        if self.selectedMessage:
            requestContent = self.selectedMessage.getRequest()
            requestInfo = self._helpers.analyzeRequest(self.selectedMessage)
            body = requestContent[requestInfo.getBodyOffset():].tostring()  # <str> type
            if len(body) == 0:
                return
            self.beautifierPanel.setText(body)

    def sendResponseToBeautifier(self, e):
        if self.selectedMessage:
            responseContent = self.selectedMessage.getResponse()
            responseInfo = self._helpers.analyzeResponse(responseContent)
            body = responseContent[responseInfo.getBodyOffset():].tostring()  # <str> type
            if len(body) == 0:
                return
            self.beautifierPanel.setText(body)


    # implement ITab
    def getTabCaption(self):
        return "Beautifier"

    # implement ITab
    def getUiComponent(self):
        return self.mainTabbedPane

    # implement IHttpListener
    def processHttpMessage(self, toolFlag, messageIsRequest, messageInfo):
        if toolFlag != IBurpExtenderCallbacks.TOOL_PROXY or messageIsRequest:
            return
        if not options.get("replaceProxyResponse").get("enable"):
            return

        requestInfo = self._helpers.analyzeRequest(messageInfo)
        url = requestInfo.getUrl().toString()
        # remove the port part
        if url.startswith("https://"):
            url = url.replace(":443", "", 1)
        if url.startswith("http://"):
            url = url.replace(":80", "", 1)

        responseContent = messageInfo.getResponse()
        responseInfo = self._helpers.analyzeResponse(responseContent)
        head = responseContent[:responseInfo.getBodyOffset()].tostring()
        body = responseContent[responseInfo.getBodyOffset():].tostring()
        if len(body) == 0:
            return

        format = None
        contentType = None
        for header in responseInfo.getHeaders():
            if header.lower().startswith("content-type:"):
                contentType = header.split(":")[1].lower().strip()
        if contentType:
            format = contentType2Format(contentType)

        if format is None:
            # Maybe the content-type of response is text/plain but the body is JSON
            format = mimeType2Format(responseInfo.getInferredMimeType())

        if format is None:
            return

        if not options.get("replaceProxyResponse").get("formats").get(format):
            return

        includePatterns = options.get("replaceProxyResponse").get("include", [])
        if includePatterns:
            isInclude = False
            for p in includePatterns:
                if p in url:
                    isInclude = True
                    break
        else:
            isInclude = True

        excludePatterns = options.get("replaceProxyResponse").get("exclude", [])
        if excludePatterns:
            isExclude = False
            for p in excludePatterns:
                if p in url:
                    isExclude = True
                    break
        else:
            isExclude = False

        if isInclude and not isExclude:
            try:
                result = beautify(body, format, raise_exception=True)
                head = re.sub(r"(?i)\r\ncontent\-length:[ \t]*\d+\r\n", "\r\nContent-Length: %d\r\n" % len(result), head)
                messageInfo.setResponse(head + result)
                print("[+] Replace Response of %s (%s) " % (url, format))
            except BeautifyException as e:
                print("[-] Replace Response of %s (%s) error: %s" %(url, format, e))

    def loadOptions(self):
        optionsStr = self._callbacks.loadExtensionSetting("options")  # <unicode> or None
        if optionsStr and len(optionsStr) > 0:
            try:
                optionsJson = json.loads(optionsStr)
                options.update(optionsJson)
            except:
                return


class BeautifierTab(IMessageEditorTab):
    def __init__(self, extender, controller, editable):
        self._extender = extender
        self._editable = editable

        # create an instance of Burp's text editor to display
        self._txtInput = extender._callbacks.createTextEditor()
        self._txtInput.setEditable(editable)

        self.currentBeautifyThread = None

    # implement IMessageEditorTab
    def getTabCaption(self):
        return "Beautifier"

    def getUiComponent(self):
        return self._txtInput.getComponent()

    def isEnabled(self, content, isRequest):
        if isRequest == True:
            requestResponseInfo = self._extender._helpers.analyzeRequest(content)
        else:
            requestResponseInfo = self._extender._helpers.analyzeResponse(content)
        # variable "content" is <array.array> object
        body = content[requestResponseInfo.getBodyOffset():].tostring()
        if len(body) == 0:
            return False

        format = None
        contentType = None
        for header in requestResponseInfo.getHeaders():
            if header.lower().startswith("content-type:"):
                contentType = header.split(":")[1].lower().strip()
        if contentType:
            format = contentType2Format(contentType)

        if format is None and isRequest == False:
            format = mimeType2Format(requestResponseInfo.getInferredMimeType())

        if format is None:
            return False

        if not options.get("messageEditorTabFormat").get(format):
            return False

        self._txtInput.setText("Beautifying ...")
        self.asyncBeautify(body, format)
        return True

    def asyncBeautify(self, data, format):
        if self.currentBeautifyThread and self.currentBeautifyThread.isAlive():
            # TODO Need a graceful way to shutdown running beautify thread.
            self.currentBeautifyThread.callback = None

        self.currentBeautifyThread = None

        def beautifyCallback(result):
            self._txtInput.setText(result)

        self.currentBeautifyThread = BeautifyThread(data, format, beautifyCallback)
        self.currentBeautifyThread.start()

    def setMessage(self, content, isRequest):
        pass

    def getMessage(self):
        pass

    def isModified(self):
        return self._txtInput.isTextModified()

    def getSelectedData(self):
        return self._txtInput.getSelectedText()


class BeautifierPanel(JPanel):
    def __init__(self):
        super(BeautifierPanel, self).__init__()
        self.setLayout(BorderLayout())

        self.beautifyTextArea = JTextArea(5, 10)
        self.beautifyTextArea.setLineWrap(True)
        self.beautifyTextArea.setDocument(self.CustomUndoPlainDocument())
        # The undo doesn't work well before replace text. Below is rough fix, so not need to know how undo work for now
        self.beautifyTextArea.setText(" "); self.beautifyTextArea.setText("")

        self.undoManager = UndoManager()
        self.beautifyTextArea.getDocument().addUndoableEditListener(self.undoManager)
        self.beautifyTextArea.getDocument().addDocumentListener(self.BeautifyDocumentListener(self))

        beautifyTextWrapper = JPanel(BorderLayout())
        beautifyScrollPane = JScrollPane(self.beautifyTextArea)
        beautifyTextWrapper.add(beautifyScrollPane, BorderLayout.CENTER)
        self.add(beautifyTextWrapper, BorderLayout.CENTER)

        self.beautifyButton = JButton("Beautify")
        self.beautifyButton.addActionListener(self.beautifyListener)
        self.undoButton = JButton("Undo")
        self.undoButton.addActionListener(self.undoListener)

        formatLabel = JLabel("Format:")
        self.formatsComboBox = JComboBox()
        for f in supportedFormats:
            self.formatsComboBox.addItem(f)

        self.statusLabel = JLabel("Status: Ready")
        preferredDimension = self.statusLabel.getPreferredSize()
        self.statusLabel.setPreferredSize(Dimension(preferredDimension.width+20, preferredDimension.height))
        self.sizeLabel = JLabel("0 B")
        preferredDimension = self.sizeLabel.getPreferredSize()
        self.sizeLabel.setPreferredSize(Dimension(preferredDimension.width + 64, preferredDimension.height))
        self.sizeLabel.setHorizontalAlignment(SwingConstants.RIGHT)


        buttonsPanel = JPanel(FlowLayout())
        buttonsPanel.add(formatLabel)
        buttonsPanel.add(self.formatsComboBox)
        buttonsPanel.add(Box.createHorizontalStrut(10))
        buttonsPanel.add(self.beautifyButton)
        buttonsPanel.add(self.undoButton)

        bottomPanel = JPanel(BorderLayout())
        bottomPanel.add(self.statusLabel, BorderLayout.WEST)
        bottomPanel.add(buttonsPanel, BorderLayout.CENTER)
        bottomPanel.add(self.sizeLabel, BorderLayout.EAST)
        self.add(bottomPanel, BorderLayout.SOUTH)

        self.currentBeautifyThread = None

    class CustomUndoPlainDocument(PlainDocument):
        # Code from: https://stackoverflow.com/questions/24433089/jtextarea-settext-undomanager
        compoundEdit = CompoundEdit()

        def fireUndoableEditUpdate(self, e):
            if self.compoundEdit == None:
                super(BeautifierPanel.CustomUndoPlainDocument, self).fireUndoableEditUpdate(e)
            else:
                self.compoundEdit.addEdit(e.getEdit())

        def replace(self, offset, length, text, attrs):
            if length == 0:
                super(BeautifierPanel.CustomUndoPlainDocument, self).replace(offset, length, text, attrs)
            else:
                self.compoundEdit = CompoundEdit()
                super(BeautifierPanel.CustomUndoPlainDocument, self).fireUndoableEditUpdate(UndoableEditEvent(self, self.compoundEdit))
                super(BeautifierPanel.CustomUndoPlainDocument, self).replace(offset, length, text, attrs)
                self.compoundEdit.end()
                self.compoundEdit = None

    def setText(self, text):
        self.beautifyTextArea.setText(text)

    def setRunningState(self):
        self.beautifyButton.setText("Cancel")
        self.undoButton.setEnabled(False)
        self.statusLabel.setText("Status: Running")

    def setReadyState(self):
        self.beautifyButton.setText("Beautify")
        self.undoButton.setEnabled(True)
        self.statusLabel.setText("Status: Ready")

    class BeautifyDocumentListener(DocumentListener):
        def __init__(self, beautifierPanel):
            super(BeautifierPanel.BeautifyDocumentListener, self).__init__()
            self.beautifierPanel = beautifierPanel

        def removeUpdate(self, e):
            self.updateSizeLabel()

        def insertUpdate(self, e):
            self.updateSizeLabel()

        def changedUpdate(self, e):
            pass

        def updateSizeLabel(self):
            length = len(self.beautifierPanel.beautifyTextArea.getText())
            if length >= 1024:
                length = "%.2f KB" % (length / 1024.0)
            else:
                length = "%d B" % length
            self.beautifierPanel.sizeLabel.setText(length)

    def beautifyListener(self, e):
        selectedFormat = self.formatsComboBox.getSelectedItem()
        data = self.beautifyTextArea.getText()  # variable "data" is "unicode" type

        if self.currentBeautifyThread and self.currentBeautifyThread.isAlive():
            # TODO Need a graceful way to shutdown running beautify thread.
            self.currentBeautifyThread.callback = None
            self.currentBeautifyThread = None
            self.setReadyState()
        else:
            self.currentBeautifyThread = None
            self.setRunningState()

            def beautifyCallback(result):
                self.beautifyTextArea.setText(result)
                self.setReadyState()

            self.currentBeautifyThread = BeautifyThread(data, selectedFormat, beautifyCallback)
            self.currentBeautifyThread.start()


    def undoListener(self, e):
        if self.undoManager.canUndo():
            self.undoManager.undo()


class BeautifierOptionsPanel(JScrollPane):
    def __init__(self, extender):
        super(BeautifierOptionsPanel, self).__init__()
        self._extender = extender

        self.contentWrapper = JPanel(GridBagLayout())
        self.setViewportView(self.contentWrapper)
        self.getVerticalScrollBar().setUnitIncrement(16)
        self.addMouseListener(self.RequestFocusListener(self))  # Let textArea lose focus when click empty area

        innerContainer = JPanel(GridBagLayout())
        innerContainer.setFocusable(True)  # make sure the maxSizeText TextField is not focused when BeautifierOptionsPanel display

        # generalOptionPanel and it's inner component
        maxSizeLabel = JLabel("Max Size: ")
        self.maxSizeText = JTextField(5)
        self.maxSizeText.setHorizontalAlignment(SwingConstants.RIGHT)
        self.maxSizeText.addFocusListener(self.MaxSizeTextListener(self))
        sizeUnitLabel = JLabel("KB")

        generalOptionPanel = JPanel(GridBagLayout())
        generalOptionPanel.setBorder(BorderFactory.createTitledBorder("General Options"))
        gbc = GridBagConstraints()
        gbc.anchor = GridBagConstraints.WEST
        gbc.gridx = 0
        gbc.gridy = 0
        generalOptionPanel.add(maxSizeLabel, gbc)
        gbc.fill = GridBagConstraints.HORIZONTAL
        gbc.gridx = 1
        gbc.gridy = 0
        generalOptionPanel.add(self.maxSizeText, gbc)
        gbc.fill = GridBagConstraints.NONE
        gbc.gridx = 2
        gbc.gridy = 0
        gbc.weightx = 1.0
        generalOptionPanel.add(sizeUnitLabel, gbc)

        gbc = GridBagConstraints()
        gbc.fill = GridBagConstraints.BOTH
        gbc.gridx = 1
        gbc.gridy = 0
        gbc.gridheight = 2
        innerContainer.add(generalOptionPanel, gbc)

        # messageTabOptionPanel and it's inner component
        self.messageTabFormatCheckBoxs = []
        for f in supportedFormats:
            ckb = JCheckBox(f)
            ckb.addItemListener(self.messageTabFormatListener)
            self.messageTabFormatCheckBoxs.append(ckb)

        messageTabOptionPanel = JPanel()
        messageTabOptionPanel.setLayout(BoxLayout(messageTabOptionPanel, BoxLayout.Y_AXIS))
        messageTabOptionPanel.setBorder(BorderFactory.createTitledBorder("Enable in MessageEditorTab"))
        for b in self.messageTabFormatCheckBoxs:
            messageTabOptionPanel.add(b)

        gbc.gridx = 1
        gbc.gridy = 2
        gbc.gridheight = 9

        innerContainer.add(messageTabOptionPanel, gbc)

        # replaceResponsePanel and it's inner component
        self.chkEnableReplace = JCheckBox("Enable")
        self.chkEnableReplace.addItemListener(self.repalceResponseBoxListener)
        replaceResponseFormatLabel = JLabel("Format")
        self.replaceResponseFormatCheckBoxs = []
        for f in supportedFormats:
            ckb = JCheckBox(f)
            ckb.addItemListener(self.replaceResponseFormatListener)
            self.replaceResponseFormatCheckBoxs.append(ckb)
        replaceResponseIncludeLabel = JLabel("Include URL that matches below(one item one line)")
        self.URLIncludeTextArea = JTextArea(6, 32)
        self.URLIncludeTextArea.addFocusListener(self.URLIncludeFocusListener(self))
        URLIncludeScrollPane = JScrollPane(self.URLIncludeTextArea)
        URLIncludeScrollPane.setAlignmentX(Component.LEFT_ALIGNMENT)
        replaceResponseExcludeLabel = JLabel("Exclude URL that matches below(one item one line)")
        self.URLExcludeTextArea = JTextArea(5, 32)
        self.URLExcludeTextArea.addFocusListener(self.URLExcludeFocusListener(self))
        URLExcludeScrollPane = JScrollPane(self.URLExcludeTextArea)
        URLExcludeScrollPane.setAlignmentX(Component.LEFT_ALIGNMENT)

        replaceResponsePanel = JPanel()
        replaceResponsePanel.setLayout(BoxLayout(replaceResponsePanel, BoxLayout.Y_AXIS))
        replaceResponsePanel.setBorder(BorderFactory.createTitledBorder("Replace PROXY Response"))
        replaceResponsePanel.add(self.chkEnableReplace)
        replaceResponsePanel.add(Box.createVerticalStrut(10))
        replaceResponsePanel.add(replaceResponseFormatLabel)
        for b in self.replaceResponseFormatCheckBoxs:
            replaceResponsePanel.add(b)
        replaceResponsePanel.add(Box.createVerticalStrut(10))
        replaceResponsePanel.add(replaceResponseIncludeLabel)
        replaceResponsePanel.add(URLIncludeScrollPane)
        replaceResponsePanel.add(Box.createVerticalStrut(10))
        replaceResponsePanel.add(replaceResponseExcludeLabel)
        replaceResponsePanel.add(URLExcludeScrollPane)

        gbc.gridy = 11
        innerContainer.add(replaceResponsePanel, gbc)

        # let innerContainer keep away from left and up
        gbc = GridBagConstraints()
        gbc.gridx = 1
        gbc.gridy = 1
        self.contentWrapper.add(Box.createHorizontalStrut(15), gbc)

        # gbc.ipadx = gbc.ipady = 25
        gbc.gridx = 2
        self.contentWrapper.add(innerContainer, gbc)

        # let innerContainer stay left side
        gbc = GridBagConstraints()
        gbc.gridx = 3
        gbc.gridy = 2
        gbc.gridwidth = 1
        gbc.weightx = gbc.weighty = 1
        paddingPanel = JPanel()
        self.contentWrapper.add(paddingPanel, gbc)

        self.setDefaultOptionDisplay()

    def disableReplaceResponseDisplay(self):
        for chb in self.replaceResponseFormatCheckBoxs:
            chb.setEnabled(False)
        self.URLIncludeTextArea.setEnabled(False)
        self.URLExcludeTextArea.setEnabled(False)

    def enableReplaceResponseDisplay(self):
        for chb in self.replaceResponseFormatCheckBoxs:
            chb.setEnabled(True)
        self.URLIncludeTextArea.setEnabled(True)
        self.URLExcludeTextArea.setEnabled(True)

    def setDefaultOptionDisplay(self):
        self.maxSizeText.setText(str(options.get("general").get("dataMaxSize") / 1024))

        for chb in self.messageTabFormatCheckBoxs:
            format = chb.getText()
            chb.setSelected(options.get("messageEditorTabFormat").get(format))

        self.chkEnableReplace.setSelected(options.get("replaceProxyResponse").get("enable"))
        for chb in self.replaceResponseFormatCheckBoxs:
            format = chb.getText()
            chb.setSelected(options.get("replaceProxyResponse").get("formats").get(format))

        self.URLIncludeTextArea.setText("\n".join(options.get("replaceProxyResponse").get("include",[])))
        self.URLExcludeTextArea.setText("\n".join(options.get("replaceProxyResponse").get("exclude",[])))

        if self.chkEnableReplace.isSelected():
            self.enableReplaceResponseDisplay()
        else:
            self.disableReplaceResponseDisplay()

    def saveOptions(self):
        if self._extender:
            self._extender._callbacks.saveExtensionSetting("options", json.dumps(options))

    class RequestFocusListener(MouseAdapter):
        def __init__(self, beautifierOptionsPanel):
            super(BeautifierOptionsPanel.RequestFocusListener, self).__init__()
            self.beautifierOptionsPanel = beautifierOptionsPanel

        def mouseClicked(self, e):
            self.beautifierOptionsPanel.requestFocusInWindow()

    class MaxSizeTextListener(FocusListener):
        def __init__(self, beautifierOptionsPanel):
            super(BeautifierOptionsPanel.MaxSizeTextListener, self).__init__()
            self.beautifierOptionsPanel = beautifierOptionsPanel

        def focusGained(self, e):
            pass

        def focusLost(self, e):
            size = e.getSource().getText()
            try:
                size = int(float(size))
                options.get("general").update({"dataMaxSize": size * 1024})
                e.getSource().setText(str(size))
                self.beautifierOptionsPanel.saveOptions()
            except:
                e.getSource().setText(str(options.get("general").get("dataMaxSize") / 1024))

    def messageTabFormatListener(self, e):
        format = e.getSource().getText()
        if e.getStateChange() == ItemEvent.SELECTED:
            options.get("messageEditorTabFormat").update({format: True})
        else:
            options.get("messageEditorTabFormat").update({format: False})
        self.saveOptions()

    def repalceResponseBoxListener(self, e):
        if e.getStateChange() == ItemEvent.SELECTED:
            options.get("replaceProxyResponse").update({"enable": True})
            self.enableReplaceResponseDisplay()
        else:
            options.get("replaceProxyResponse").update({"enable": False})
            self.disableReplaceResponseDisplay()
        self.saveOptions()

    def replaceResponseFormatListener(self, e):
        format = e.getSource().getText()
        if e.getStateChange() == ItemEvent.SELECTED:
            options.get("replaceProxyResponse").get("formats").update({format: True})
        else:
            options.get("replaceProxyResponse").get("formats").update({format: False})
        self.saveOptions()

    class URLIncludeFocusListener(FocusListener):
        def __init__(self, beautifierOptionsPanel):
            super(BeautifierOptionsPanel.URLIncludeFocusListener, self).__init__()
            self.beautifierOptionsPanel = beautifierOptionsPanel

        def focusGained(self, e):
            pass

        def focusLost(self, e):
            text = e.getSource().getText()  # <unicode>
            urlPatterns = [p.strip() for p in text.split("\n") if p.strip() != ""]
            options.get("replaceProxyResponse").update({"include": urlPatterns})
            self.beautifierOptionsPanel.saveOptions()

    class URLExcludeFocusListener(FocusListener):
        def __init__(self, beautifierOptionsPanel):
            super(BeautifierOptionsPanel.URLExcludeFocusListener, self).__init__()
            self.beautifierOptionsPanel = beautifierOptionsPanel

        def focusGained(self, e):
            pass

        def focusLost(self, e):
            text = e.getSource().getText()  # <unicode>
            urlPatterns = [p.strip() for p in text.split("\n") if p.strip() != ""]
            options.get("replaceProxyResponse").update({"exclude": urlPatterns})
            self.beautifierOptionsPanel.saveOptions()


def main():
    """
    For debugging swing GUI convenientlys
    """
    class SwingExample:
        def __init__(self):
            self.mainFrame = None
            self.mainTabbedPane = JTabbedPane()

        def start(self):
            self.mainFrame = JFrame("SwingTest")
            self.mainFrame.setSize(800, 600)
            self.mainFrame.setLocationRelativeTo(None)
            self.mainFrame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE)
            self.mainFrame.add(self.mainTabbedPane)
            self.mainFrame.setVisible(True)

        def addToTabbedPane(self, tabName, component):
            self.mainTabbedPane.addTab(tabName, component)

        def main(self):
            self.addToTabbedPane("Beautify", BeautifierPanel())
            self.addToTabbedPane("Options", BeautifierOptionsPanel(None))

            self.start()

    SwingExample().main()


if __name__ == "__main__" and len(__import__("sys").argv[0]) > 0:
    """
    If this script run as burpsuite extension, sys.argv[0] is empty string.
    So use sys.argv[0] to determine whether it run as burpsuite extension.
    """
    main()



