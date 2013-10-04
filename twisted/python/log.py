# -*- test-case-name: twisted.test.test_log -*-
# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Logging and metrics infrastructure.
"""

from __future__ import division, absolute_import

import sys
import time
import warnings
import logging

from zope.interface import Interface

from twisted.python.compat import unicode, _PY3
from twisted.python import context
from twisted.python import _reflectpy3 as reflect
from twisted.python import failure
from twisted.python.threadable import synchronize
from twisted.python.logger import (
    LogLevel as NewLogLevel,
    Logger as NewLogger,
    FileLogObserver as NewFileLogObserver,
    PythonLogObserver as NewPythonLogObserver,
    LegacyLogObserverWrapper, LoggingFile,
)



class ILogContext:
    """
    Actually, this interface is just a synonym for the dictionary interface,
    but it serves as a key for the default information in a log.

    I do not inherit from C{Interface} because the world is a cruel place.
    """



class ILogObserver(Interface):
    """
    An observer which can do something with log events.

    Given that most log observers are actually bound methods, it's okay to not
    explicitly declare provision of this interface.
    """
    def __call__(eventDict):
        """
        Log an event.

        @type eventDict: C{dict} with C{str} keys.
        @param eventDict: A dictionary with arbitrary keys.  However, these
            keys are often available:
              - C{message}: A C{tuple} of C{str} containing messages to be
                logged.
              - C{system}: A C{str} which indicates the "system" which is
                generating this event.
              - C{isError}: A C{bool} indicating whether this event represents
                an error.
              - C{failure}: A L{failure.Failure} instance
              - C{why}: Used as header of the traceback in case of errors.
              - C{format}: A string format used in place of C{message} to
                customize the event.  The intent is for the observer to format
                a message by doing something like C{format % eventDict}.
        """



context.setDefault(ILogContext,
                   {"isError": 0,
                    "system": "-"})

def callWithContext(ctx, func, *args, **kw):
    newCtx = context.get(ILogContext).copy()
    newCtx.update(ctx)
    return context.call({ILogContext: newCtx}, func, *args, **kw)

def callWithLogger(logger, func, *args, **kw):
    """
    Utility method which wraps a function in a try:/except:, logs a failure if
    one occurrs, and uses the system's logPrefix.
    """
    try:
        lp = logger.logPrefix()
    except KeyboardInterrupt:
        raise
    except:
        lp = '(buggy logPrefix method)'
        err(system=lp)
    try:
        return callWithContext({"system": lp}, func, *args, **kw)
    except KeyboardInterrupt:
        raise
    except:
        err(system=lp)



def err(_stuff=None, _why=None, **kw):
    """
    Write a failure to the log.

    The C{_stuff} and C{_why} parameters use an underscore prefix to lessen
    the chance of colliding with a keyword argument the application wishes
    to pass.  It is intended that they be supplied with arguments passed
    positionally, not by keyword.

    @param _stuff: The failure to log.  If C{_stuff} is C{None} a new
        L{Failure} will be created from the current exception state.  If
        C{_stuff} is an C{Exception} instance it will be wrapped in a
        L{Failure}.
    @type _stuff: C{NoneType}, C{Exception}, or L{Failure}.

    @param _why: The source of this failure.  This will be logged along with
        C{_stuff} and should describe the context in which the failure
        occurred.
    @type _why: C{str}
    """
    if _stuff is None:
        _stuff = failure.Failure()
    if isinstance(_stuff, failure.Failure):
        msg(failure=_stuff, why=_why, isError=1, **kw)
    elif isinstance(_stuff, Exception):
        msg(failure=failure.Failure(_stuff), why=_why, isError=1, **kw)
    else:
        msg(repr(_stuff), why=_why, isError=1, **kw)

deferr = err


class Logger:
    """
    This represents a class which may 'own' a log. Used by subclassing.
    """
    def logPrefix(self):
        """
        Override this method to insert custom logging behavior.  Its
        return value will be inserted in front of every line.  It may
        be called more times than the number of output lines.
        """
        return '-'



class LogPublisher:
    """
    Class for singleton log message publishing.
    """

    synchronized = ['msg']


    @property
    def observers(self):
        return [
            observer.legacyObserver for observer
            in NewLogger.publisher.filteredPublisher.observers
            if getattr(observer, "originator", None) == self
        ]


    def addObserver(self, other):
        """
        Add a new observer.

        @type other: Provider of L{ILogObserver}
        @param other: A callable object that will be called with each new log
            message (a dict).
        """
        wrapped = LegacyLogObserverWrapper(other)
        wrapped.originator = self
        NewLogger.publisher.filteredPublisher.addObserver(wrapped)


    def removeObserver(self, other):
        """
        Remove an observer.
        """
        for observer in NewLogger.publisher.filteredPublisher.observers:
            if (
                getattr(observer, "originator", None) == self and
                observer.legacyObserver == other
            ):
                NewLogger.publisher.filteredPublisher.removeObserver(observer)
                break


    def msg(self, *message, **kw):
        """
        Log a new message.

        The message should be a native string, i.e. bytes on Python 2 and
        Unicode on Python 3. For compatibility with both use the native string
        syntax, for example::

        >>> log.msg('Hello, world.')

        You MUST avoid passing in Unicode on Python 2, and the form::

        >>> log.msg('Hello ', 'world.')

        This form only works (sometimes) by accident.
        """
        actualEventDict = (context.get(ILogContext) or {}).copy()
        actualEventDict.update(kw)
        actualEventDict['message'] = message
        actualEventDict['time'] = time.time()

        publishToNewObserver(NewLogger.publisher, actualEventDict)


    def showwarning(self, message, category, filename, lineno, file=None,
                    line=None):
        """
        Twisted-enabled wrapper around L{warnings.showwarning}.

        If C{file} is C{None}, the default behaviour is to emit the warning to
        the log system, otherwise the original L{warnings.showwarning} Python
        function is called.
        """
        if file is None:
            self.msg(warning=message, category=reflect.qual(category),
                     filename=filename, lineno=lineno,
                     format="%(filename)s:%(lineno)s: %(category)s: %(warning)s")
        else:
            if sys.version_info < (2, 6):
                _oldshowwarning(message, category, filename, lineno, file)
            else:
                _oldshowwarning(message, category, filename, lineno, file, line)


synchronize(LogPublisher)



try:
    theLogPublisher
except NameError:
    theLogPublisher = LogPublisher()
    addObserver = theLogPublisher.addObserver
    removeObserver = theLogPublisher.removeObserver
    msg = theLogPublisher.msg
    showwarning = theLogPublisher.showwarning



def _safeFormat(fmtString, fmtDict):
    """
    Try to format the string C{fmtString} using C{fmtDict} arguments,
    swallowing all errors to always return a string.
    """
    # There's a way we could make this if not safer at least more
    # informative: perhaps some sort of str/repr wrapper objects
    # could be wrapped around the things inside of C{fmtDict}. That way
    # if the event dict contains an object with a bad __repr__, we
    # can only cry about that individual object instead of the
    # entire event dict.
    try:
        text = fmtString % fmtDict
    except KeyboardInterrupt:
        raise
    except:
        try:
            text = ('Invalid format string or unformattable object in log message: %r, %s' % (fmtString, fmtDict))
        except:
            try:
                text = 'UNFORMATTABLE OBJECT WRITTEN TO LOG with fmt %r, MESSAGE LOST' % (fmtString,)
            except:
                text = 'PATHOLOGICAL ERROR IN BOTH FORMAT STRING AND MESSAGE DETAILS, MESSAGE LOST'
    return text


def textFromEventDict(eventDict):
    """
    Extract text from an event dict passed to a log observer. If it cannot
    handle the dict, it returns None.

    The possible keys of eventDict are:
     - C{message}: by default, it holds the final text. It's required, but can
       be empty if either C{isError} or C{format} is provided (the first
       having the priority).
     - C{isError}: boolean indicating the nature of the event.
     - C{failure}: L{failure.Failure} instance, required if the event is an
       error.
     - C{why}: if defined, used as header of the traceback in case of errors.
     - C{format}: string format used in place of C{message} to customize
       the event. It uses all keys present in C{eventDict} to format
       the text.
    Other keys will be used when applying the C{format}, or ignored.
    """
    edm = eventDict['message']
    if not edm:
        if eventDict['isError'] and 'failure' in eventDict:
            text = ((eventDict.get('why') or 'Unhandled Error')
                    + '\n' + eventDict['failure'].getTraceback())
        elif 'format' in eventDict:
            text = _safeFormat(eventDict['format'], eventDict)
        else:
            # we don't know how to log this
            return
    else:
        text = ' '.join(map(reflect.safe_str, edm))
    return text


class StartStopMixIn:
    def start(self):
        """
        Start observing log events.
        """
        addObserver(self.emit)

    def stop(self):
        """
        Stop observing log events.
        """
        removeObserver(self.emit)


class FileLogObserver(StartStopMixIn):
    """
    Log observer that writes to a file-like object.

    @type timeFormat: C{str} or C{NoneType}
    @ivar timeFormat: If not C{None}, the format string passed to strftime().
    """
    defaultTimeFormat = "%d-%02d-%02d %02d:%02d:%02d%s%02d%02d%z"

    def __init__(self, f):
        self._f = f
        self._timeFormat = None
        self._initObserver()

    def _initObserver(self):
        timeFormat = self.timeFormat
        if timeFormat is None:
            timeFormat = self.defaultTimeFormat

        self._newFLO = NewFileLogObserver(self._f, timeFormat=timeFormat)

    @property
    def timeFormat(self):
        return self._timeFormat

    @timeFormat.setter
    def timeFormat(self, timeFormat):
        # If timeFormat is set, we need to replace the NewFLO
        self._timeFormat = timeFormat
        self._initObserver()

    def emit(self, eventDict):
        publishToNewObserver(self._newFLO, eventDict)


class PythonLoggingObserver(NewPythonLogObserver, StartStopMixIn):
    """
    Output twisted messages to Python standard library L{logging} module.

    WARNING: specific logging configurations (example: network) can lead to
    a blocking system. Nothing is done here to prevent that, so be sure to not
    use this: code within Twisted, such as twisted.web, assumes that logging
    does not block.
    """

    def __init__(self, loggerName="twisted"):
        """
        @param loggerName: identifier used for getting logger.
        @type loggerName: C{str}
        """
        NewPythonLogObserver.__init__(self, loggerName)

    def emit(self, eventDict):
        """
        Receive a twisted log entry, format it and bridge it to python.

        By default the logging level used is info; log.err produces error
        level, and you can customize the level by using the C{logLevel} key::

        >>> log.msg('debugging', logLevel=logging.DEBUG)

        """
        publishToNewObserver(self, eventDict)



class StdioOnnaStick:
    """
    Class that pretends to be stdout/err, and turns writes into log messages.

    @ivar isError: boolean indicating whether this is stderr, in which cases
                   log messages will be logged as errors.

    @ivar encoding: unicode encoding used to encode any unicode strings
                    written to this object.
    """

    closed = 0
    softspace = 0
    mode = 'wb'
    name = '<stdio (log)>'

    def __init__(self, isError=0, encoding=None):
        self.isError = isError
        if encoding is None:
            encoding = sys.getdefaultencoding()
        self.encoding = encoding
        self.buf = ''
  
    def close(self):
        pass

    def fileno(self):
        return -1

    def flush(self):
        pass

    def read(self):
        raise IOError("can't read from the log!")

    readline = read
    readlines = read
    seek = read
    tell = read

    def write(self, data):
        if not _PY3 and isinstance(data, unicode):
            data = data.encode(self.encoding)
        d = (self.buf + data).split('\n')
        self.buf = d[-1]
        messages = d[0:-1]
        for message in messages:
            msg(message, printed=1, isError=self.isError)

    def writelines(self, lines):
        for line in lines:
            if not _PY3 and isinstance(line, unicode):
                line = line.encode(self.encoding)
            msg(line, printed=1, isError=self.isError)


try:
    _oldshowwarning
except NameError:
    _oldshowwarning = None


def startLogging(file, *a, **kw):
    """
    Initialize logging to a specified file.

    @return: A L{FileLogObserver} if a new observer is added, None otherwise.
    """
    if isinstance(file, LoggingFile):
        return
    flo = FileLogObserver(file)
    startLoggingWithObserver(flo.emit, *a, **kw)
    return flo



def startLoggingWithObserver(observer, setStdout=1):
    """
    Initialize logging to a specified observer. If setStdout is true
    (defaults to yes), also redirect sys.stdout and sys.stderr
    to the specified file.
    """
    global defaultObserver, _oldshowwarning
    if not _oldshowwarning:
        _oldshowwarning = warnings.showwarning
        warnings.showwarning = showwarning
    if defaultObserver:
        defaultObserver.stop()
        defaultObserver = None
    addObserver(observer)
    msg("Log opened.")
    if setStdout:
        sys.stdout = logfile
        sys.stderr = logerr


class NullFile:
    softspace = 0
    def read(self): pass
    def write(self, bytes): pass
    def flush(self): pass
    def close(self): pass


def discardLogs():
    """
    Throw away all logs.
    """
    global logfile
    logfile = NullFile()


# Prevent logfile from being erased on reload.  This only works in cpython.
try:
    logfile
except NameError:
    logfile = LoggingFile(level=NewLogLevel.info, encoding=getattr(sys.stdout, "encoding", None))
    logerr = LoggingFile(level=NewLogLevel.error, encoding=getattr(sys.stderr, "encoding", None))



class DefaultObserver(StartStopMixIn):
    """
    Default observer.

    Will ignore all non-error messages and send error messages to sys.stderr.
    Will be removed when startLogging() is called for the first time.
    """
    stderr = sys.stderr

    def emit(self, eventDict):
        if eventDict["isError"]:
            if 'failure' in eventDict:
                text = ((eventDict.get('why') or 'Unhandled Error')
                        + '\n' + eventDict['failure'].getTraceback())
            else:
                text = " ".join([str(m) for m in eventDict["message"]]) + "\n"

            self.stderr.write(text)
            self.stderr.flush()



try:
    defaultObserver
except NameError:
    defaultObserver = DefaultObserver()
    defaultObserver.start()



pythonLogLevelToNewLogLevelMapping = {
    logging.DEBUG: NewLogLevel.debug,
    logging.INFO: NewLogLevel.info,
    logging.WARNING: NewLogLevel.warn,
    logging.ERROR: NewLogLevel.error,
    logging.CRITICAL: NewLogLevel.error,
}


def publishToNewObserver(observer, eventDict):
    """
    Publish an event to a new-style observer.
    """
    #
    # Note that it's possible that a new-style event was sent to a
    # LegacyLogObserverWrapper, and may now be getting sent back to a new-style
    # observer.  In this case, it's already a new-style event, adapted to also
    # look like an old-style event, and we don't need to tweak it again to be a
    # new-style event, hence the checks for already-defined new-style keys.
    #

    if "log_format" not in eventDict:
        text = textFromEventDict(eventDict)
        if text is None:
            return

        eventDict["log_text"] = text
        eventDict["log_format"] = "{log_text}"

    if "log_level" not in eventDict:
        if "logLevel" in eventDict:
            level = pythonLogLevelToNewLogLevelMapping[eventDict["logLevel"]]
        elif eventDict["isError"]:
            level = NewLogLevel.error
        else:
            level = NewLogLevel.info

        eventDict["log_level"] = level

    if "log_namespace" not in eventDict:
        eventDict["log_namespace"] = "log_legacy"

    if "log_system" not in eventDict and "system" in eventDict:
        eventDict["log_system"] = eventDict["system"]

    observer(eventDict)
