import paramiko
import os
import re
import time
import errno
import traceback
import sys
from io import StringIO
from copy import deepcopy

text_type = str
   
class spawn:

    def __init__(self,timeout=30, maxread=2000, searchwindowsize=None,username="",password="",port=0,ipaddress=""):
    
        self.ssh = paramiko.SSHClient()
        self.channel = None  
        self.searcher = None
        self.username = username
        self.password = password
        self.port = port
        self.ipaddress = ipaddress
        self.child_fd = -1
        self.timeout = timeout
        self.ignorecase=False
        self.delimiter = EOF
        self.closed=True
        self.before = None
        self.after = None
        self.match = None
        self.match_index = None
        self.linesep = os.linesep.encode('ascii')
        self.string_type = text_type
        self.buffer_type = StringIO
        self.maxread = maxread
        self.searchwindowsize = searchwindowsize
        self.crlf = u'\r\n'
        self.allowed_string_types = (text_type, )
        self.delaybeforesend = 0.05
        self.delayafterclose = 0.1
        self.delayafterterminate = 0.1
        self.delayafterread = 0.0001
        self.softspace = False
        self.name = '<' + repr(self) + '>'
        self._buffer = self.buffer_type()
        self._before = self.buffer_type()
        self._spawn(self.ipaddress,self.username,self.password,self.port)
        
    def _spawn(self,ipaddress,username,password,port):
        ssh = self.ssh
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ipaddress,username=username,password=password,port=port)
        self.channel = self.ssh.invoke_shell()
        self.child_fd = self.channel.makefile('w')
        self.closed=False

    def _get_buffer(self):
        return self._buffer.getvalue()

    def _set_buffer(self, value):
        self._buffer = self.buffer_type()
        self._buffer.write(value)
    
    buffer = property(_get_buffer, _set_buffer)
        
    def read_nonblocking(self, size=1, timeout=None):
        if self.closed:
            raise ValueError('I/O operation on closed file.')
        self.channel.settimeout(timeout)
        if self.channel.recv_ready():
            s = remove_format(self.channel.recv(size).decode())
            return s   
        return ""
        
    def close(self):
        self.ssh.close()
        self.closed=True
    def send(self,sendStr):
        self.child_fd.write(sendStr)
        return len(sendStr)
    def sendln(self,sendStr):
        return self.send(sendStr + '\r')
    def write(self, writeStr):
        self.send(writeStr)
    def writelines(self, writeList):
        for str in writeList:
            self.sendln(str)
    def parsebefore(self,split=' ',trigger="",location=None,retrigger=False,debug=False):
        triggered = False
        xpos = 0
        ypos = 0
        if trigger=="":
            return []
         
        if isinstance(trigger,str):
            trigList=[trigger[:]]
        else:
            trigList=trigger.copy()
            
        if isinstance(location[0],int):
            locList=[[location.copy()]]
        elif isinstance(location[0][0],int):
            locList=[deepcopy(location)]
        else:
            locList=deepcopy(location)
        retList = []
        
        if retrigger:
            saveTrigList = trigList.copy()
            saveLocList = deepcopy(locList)
        
        if debug:
            print ("trigList:",trigList)
            print ("locList:",locList)
            print ("saveLocList:",saveLocList)
        
        trigStr = trigList.pop(0)
        trigLocList = locList.pop(0)
        
        for line in self.before.splitlines():
            if split == ' ':
                splitline = line.split()
            else:
                splitline = [x.strip() for x in line.split(split)]
            for text in splitline:
                if debug: print (text+'('+str(xpos)+','+str(ypos)+') ',end="")
                if not triggered and text == trigStr:
                    if debug: print('<<<',end="")
                    triggered = True
                    retList.append([])
                if triggered:
                    locidx=0
                    for loc in trigLocList:
                        if xpos == loc[0] and ypos == loc[1]:
                            retList[-1].append(text)
                            trigLocList.pop(locidx)
                            if len(trigLocList)==0:
                                triggered=False
                                ypos=0
                                if len(trigList)==0:
                                    if not retrigger:
                                        return retList
                                    trigList=saveTrigList.copy()
                                    locList =deepcopy(saveLocList)
                                trigStr=trigList.pop(0)
                                trigLocList=locList.pop(0)
                                
                        locidx+=1
                xpos = xpos + 1
            xpos = 0
            if triggered:
                ypos = ypos + 1
            if debug: print ()
        return retList       
               
    def compile_pattern_list(self, patterns):
    
        if patterns is None:
            return []
        if not isinstance(patterns, list):
            patterns = [patterns]

        # Allow dot to match \n
        compile_flags = re.DOTALL
        if self.ignorecase:
            compile_flags = compile_flags | re.IGNORECASE
        compiled_pattern_list = []
        for idx, p in enumerate(patterns):
            if isinstance(p, self.allowed_string_types):
                #p = self._coerce_expect_string(p)
                compiled_pattern_list.append(re.compile(p, compile_flags))
            elif p is EOF:
                compiled_pattern_list.append(EOF)
            elif p is TIMEOUT:
                compiled_pattern_list.append(TIMEOUT)
            elif isinstance(p, type(re.compile(''))):
                compiled_pattern_list.append(p)
            else:
                self._pattern_type_err(p)
        return compiled_pattern_list

    def expect(self, pattern, timeout=-1, searchwindowsize=-1):
        
        compiled_pattern_list = self.compile_pattern_list(pattern)
        return self.expect_list(compiled_pattern_list,
                timeout, searchwindowsize)

    def expect_list(self, pattern_list, timeout=-1, searchwindowsize=-1):

        if timeout == -1:
            timeout = self.timeout
        
        exp = Expecter(self, searcher_re(pattern_list), searchwindowsize)

        return exp.expect_loop(timeout)
           
def remove_format(line):
	return re.compile(r'(\x9B|\x1B\[)[0-?]*[ -/]*[@-~]').sub('', line).replace('\b', '').replace('\r', '') 
        
class Expecter(object):
    def __init__(self, spawn, searcher, searchwindowsize=-1):
        self.spawn = spawn
        self.searcher = searcher
        # A value of -1 means to use the figure from spawn, which should
        # be None or a positive number.
        if searchwindowsize == -1:
            searchwindowsize = spawn.searchwindowsize
        self.searchwindowsize = searchwindowsize
        self.lookback = None
        if hasattr(searcher, 'longest_string'):
            self.lookback = searcher.longest_string

    def do_search(self, window, freshlen):
        spawn = self.spawn
        searcher = self.searcher
        if freshlen > len(window):
            freshlen = len(window)
        index = searcher.search(window, freshlen, self.searchwindowsize)
        if index >= 0:
            spawn._buffer = spawn.buffer_type()
            spawn._buffer.write(window[searcher.end:])
            spawn.before = spawn._before.getvalue()[
                0:-(len(window) - searcher.start)]
            spawn._before = spawn.buffer_type()
            spawn._before.write(window[searcher.end:])
            spawn.after = window[searcher.start:searcher.end]
            spawn.match = searcher.match
            spawn.match_index = index
            # Found a match
            return index
        elif self.searchwindowsize or self.lookback:
            maintain = self.searchwindowsize or self.lookback
            if spawn._buffer.tell() > maintain:
                spawn._buffer = spawn.buffer_type()
                spawn._buffer.write(window[-maintain:])

    def existing_data(self):
        # First call from a new call to expect_loop or expect_async.
        # self.searchwindowsize may have changed.
        # Treat all data as fresh.
        spawn = self.spawn
        before_len = spawn._before.tell()
        buf_len = spawn._buffer.tell()
        freshlen = before_len
        if before_len > buf_len:
            if not self.searchwindowsize:
                spawn._buffer = spawn.buffer_type()
                window = spawn._before.getvalue()
                spawn._buffer.write(window)
            elif buf_len < self.searchwindowsize:
                spawn._buffer = spawn.buffer_type()
                spawn._before.seek(
                    max(0, before_len - self.searchwindowsize))
                window = spawn._before.read()
                spawn._buffer.write(window)
            else:
                spawn._buffer.seek(max(0, buf_len - self.searchwindowsize))
                window = spawn._buffer.read()
        else:
            if self.searchwindowsize:
                spawn._buffer.seek(max(0, buf_len - self.searchwindowsize))
                window = spawn._buffer.read()
            else:
                window = spawn._buffer.getvalue()
        return self.do_search(window, freshlen)

    def new_data(self, data):
        # A subsequent call, after a call to existing_data.
        spawn = self.spawn
        freshlen = len(data)
        spawn._before.write(data)
        if not self.searchwindowsize:
            if self.lookback:
                # search lookback + new data.
                old_len = spawn._buffer.tell()
                spawn._buffer.write(data)
                spawn._buffer.seek(max(0, old_len - self.lookback))
                window = spawn._buffer.read()
            else:
                # copy the whole buffer (really slow for large datasets).
                spawn._buffer.write(data)
                window = spawn.buffer
        else:
            if len(data) >= self.searchwindowsize or not spawn._buffer.tell():
                window = data[-self.searchwindowsize:]
                spawn._buffer = spawn.buffer_type()
                spawn._buffer.write(window[-self.searchwindowsize:])
            else:
                spawn._buffer.write(data)
                new_len = spawn._buffer.tell()
                spawn._buffer.seek(max(0, new_len - self.searchwindowsize))
                window = spawn._buffer.read()
        return self.do_search(window, freshlen)

    def eof(self, err=None):
        spawn = self.spawn

        spawn.before = spawn._before.getvalue()
        spawn._buffer = spawn.buffer_type()
        spawn._before = spawn.buffer_type()
        spawn.after = EOF
        index = self.searcher.eof_index
        if index >= 0:
            spawn.match = EOF
            spawn.match_index = index
            return index
        else:
            spawn.match = None
            spawn.match_index = None
            msg = str(spawn)
            msg += '\nsearcher: %s' % self.searcher
            if err is not None:
                msg = str(err) + '\n' + msg

            exc = EOF(msg)
            exc.__cause__ = None # in Python 3.x we can use "raise exc from None"
            raise exc

    def timeout(self, err=None):
        spawn = self.spawn

        spawn.before = spawn._before.getvalue()
        spawn.after = TIMEOUT
        index = self.searcher.timeout_index
        if index >= 0:
            spawn.match = TIMEOUT
            spawn.match_index = index
            return index
        else:
            spawn.match = None
            spawn.match_index = None
            msg = str(spawn)
            msg += '\nsearcher: %s' % self.searcher
            if err is not None:
                msg = str(err) + '\n' + msg

            exc = TIMEOUT(msg)
            exc.__cause__ = None    # in Python 3.x we can use "raise exc from None"
            raise exc

    def errored(self):
        spawn = self.spawn
        spawn.before = spawn._before.getvalue()
        spawn.after = None
        spawn.match = None
        spawn.match_index = None

    def expect_loop(self, timeout=-1):
        """Blocking expect"""
        spawn = self.spawn
        if timeout is not None:
            end_time = time.time() + timeout

        try:
            idx = self.existing_data()
            if idx is not None:
                return idx
            while True:
                # No match at this point
                if (timeout is not None) and (timeout < 0):
                    return self.timeout()
                # Still have time left, so read more data
                incoming = spawn.read_nonblocking(spawn.maxread, timeout)
                if self.spawn.delayafterread is not None:
                    time.sleep(self.spawn.delayafterread)
                idx = self.new_data(incoming)
                # Keep reading until exception or return.
                if idx is not None:
                    return idx
                if timeout is not None:
                    timeout = end_time - time.time()
        except EOF as e:
            return self.eof(e)
        except TIMEOUT as e:
            return self.timeout(e)
        except:
            self.errored()
            raise


class searcher_string(object):
    '''This is a plain string search helper for the spawn.expect_any() method.
    This helper class is for speed. For more powerful regex patterns
    see the helper class, searcher_re.
    Attributes:
        eof_index     - index of EOF, or -1
        timeout_index - index of TIMEOUT, or -1
    After a successful match by the search() method the following attributes
    are available:
        start - index into the buffer, first byte of match
        end   - index into the buffer, first byte after match
        match - the matching string itself
    '''

    def __init__(self, strings):
        '''This creates an instance of searcher_string. This argument 'strings'
        may be a list; a sequence of strings; or the EOF or TIMEOUT types. '''

        self.eof_index = -1
        self.timeout_index = -1
        self._strings = []
        self.longest_string = 0
        for n, s in enumerate(strings):
            if s is EOF:
                self.eof_index = n
                continue
            if s is TIMEOUT:
                self.timeout_index = n
                continue
            self._strings.append((n, s))
            if len(s) > self.longest_string:
                self.longest_string = len(s)

    def __str__(self):
        '''This returns a human-readable string that represents the state of
        the object.'''

        ss = [(ns[0], '    %d: %r' % ns) for ns in self._strings]
        ss.append((-1, 'searcher_string:'))
        if self.eof_index >= 0:
            ss.append((self.eof_index, '    %d: EOF' % self.eof_index))
        if self.timeout_index >= 0:
            ss.append((self.timeout_index,
                '    %d: TIMEOUT' % self.timeout_index))
        ss.sort()
        ss = list(zip(*ss))[1]
        return '\n'.join(ss)

    def search(self, buffer, freshlen, searchwindowsize=None):
        '''This searches 'buffer' for the first occurrence of one of the search
        strings.  'freshlen' must indicate the number of bytes at the end of
        'buffer' which have not been searched before. It helps to avoid
        searching the same, possibly big, buffer over and over again.
        See class spawn for the 'searchwindowsize' argument.
        If there is a match this returns the index of that string, and sets
        'start', 'end' and 'match'. Otherwise, this returns -1. '''

        first_match = None

        # 'freshlen' helps a lot here. Further optimizations could
        # possibly include:
        #
        # using something like the Boyer-Moore Fast String Searching
        # Algorithm; pre-compiling the search through a list of
        # strings into something that can scan the input once to
        # search for all N strings; realize that if we search for
        # ['bar', 'baz'] and the input is '...foo' we need not bother
        # rescanning until we've read three more bytes.
        #
        # Sadly, I don't know enough about this interesting topic. /grahn

        for index, s in self._strings:
            if searchwindowsize is None:
                # the match, if any, can only be in the fresh data,
                # or at the very end of the old data
                offset = -(freshlen + len(s))
            else:
                # better obey searchwindowsize
                offset = -searchwindowsize
            n = buffer.find(s, offset)
            if n >= 0 and (first_match is None or n < first_match):
                first_match = n
                best_index, best_match = index, s
        if first_match is None:
            return -1
        self.match = best_match
        self.start = first_match
        self.end = self.start + len(self.match)
        return best_index


class searcher_re(object):
    '''This is regular expression string search helper for the
    spawn.expect_any() method. This helper class is for powerful
    pattern matching. For speed, see the helper class, searcher_string.
    Attributes:
        eof_index     - index of EOF, or -1
        timeout_index - index of TIMEOUT, or -1
    After a successful match by the search() method the following attributes
    are available:
        start - index into the buffer, first byte of match
        end   - index into the buffer, first byte after match
        match - the re.match object returned by a successful re.search
    '''

    def __init__(self, patterns):
        '''This creates an instance that searches for 'patterns' Where
        'patterns' may be a list or other sequence of compiled regular
        expressions, or the EOF or TIMEOUT types.'''

        self.eof_index = -1
        self.timeout_index = -1
        self._searches = []
        for n, s in enumerate(patterns):
            if s is EOF:
                self.eof_index = n
                continue
            if s is TIMEOUT:
                self.timeout_index = n
                continue
            self._searches.append((n, s))

    def __str__(self):
        '''This returns a human-readable string that represents the state of
        the object.'''

        #ss = [(n, '    %d: re.compile("%s")' %
        #    (n, repr(s.pattern))) for n, s in self._searches]
        ss = list()
        for n, s in self._searches:
            ss.append((n, '    %d: re.compile(%r)' % (n, s.pattern)))
        ss.append((-1, 'searcher_re:'))
        if self.eof_index >= 0:
            ss.append((self.eof_index, '    %d: EOF' % self.eof_index))
        if self.timeout_index >= 0:
            ss.append((self.timeout_index, '    %d: TIMEOUT' %
                self.timeout_index))
        ss.sort()
        ss = list(zip(*ss))[1]
        return '\n'.join(ss)

    def search(self, buffer, freshlen, searchwindowsize=None):
        '''This searches 'buffer' for the first occurrence of one of the regular
        expressions. 'freshlen' must indicate the number of bytes at the end of
        'buffer' which have not been searched before.
        See class spawn for the 'searchwindowsize' argument.
        If there is a match this returns the index of that string, and sets
        'start', 'end' and 'match'. Otherwise, returns -1.'''

        first_match = None
        # 'freshlen' doesn't help here -- we cannot predict the
        # length of a match, and the re module provides no help.
        if searchwindowsize is None:
            searchstart = 0
        else:
            searchstart = max(0, len(buffer) - searchwindowsize)
        for index, s in self._searches:
            match = s.search(buffer, searchstart)
            if match is None:
                continue
            n = match.start()
            if first_match is None or n < first_match:
                first_match = n
                the_match = match
                best_index = index
        if first_match is None:
            return -1
        self.start = first_match
        self.match = the_match
        self.end = self.match.end()
        return best_index
        
class ExceptionPexpect(Exception):
    '''Base class for all exceptions raised by this module.
    '''

    def __init__(self, value):
        super(ExceptionPexpect, self).__init__(value)
        self.value = value

    def __str__(self):
        return str(self.value)

    def get_trace(self):
        '''This returns an abbreviated stack trace with lines that only concern
        the caller. In other words, the stack trace inside the Pexpect module
        is not included. '''

        tblist = traceback.extract_tb(sys.exc_info()[2])
        tblist = [item for item in tblist if ('pexpect/__init__' not in item[0])
                                           and ('pexpect/expect' not in item[0])]
        tblist = traceback.format_list(tblist)
        return ''.join(tblist)


class EOF(ExceptionPexpect):
    '''Raised when EOF is read from a child.
    This usually means the child has exited.'''


class TIMEOUT(ExceptionPexpect):
    '''Raised when a read time exceeds the timeout. '''