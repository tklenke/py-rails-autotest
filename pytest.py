#! /usr/bin/env python3
import os
import sys
import re
import time
import subprocess
import signal

#TODO capture elapsed time from time tests submitted to completed and display
#TODO cntrl-c one time triggers full test suite run

#### DEFAULT VALUES
num_loops_between_scan_cwd = 10
secs_between_loops = 2
wait_after_ng_restart = 3
cwd = os.getcwd()
ptrn_first_filter = re.compile('.*\.(rb|erb|yml)$')
ptrn_second_filter = re.compile('^/(\.git|tmp)/.*') #don't count these skipped files
start_nailgun_server_command = 'jruby --ng-server '
DEBUG = False
VERBOSE = False
FULLTRACE = False

tracesilencers = [
    re.compile('.*/jruby\-1\.7\.0\.preview2/.*'),
    re.compile('.*(RubyBasicObject|RubyKernel|RubyProc|RubyArray).*')
]

# Test Patterns
#in priority order -- matches first and not subsequent (works like routes.rb)
testpatterns = [
    #change to app/controllers/application -- rerun all controllers
    [re.compile('^/app/controllers/application_controller\.rb$'), ['CONTROLLERS']],

    #change to app/views/layout/* -- rerun all controllers
    [re.compile('^/app/views/layouts/.*\.erb$'), ['CONTROLLERS']],

    #change to app/views/shared/ -- rerun all controller tests
    [re.compile('^/app/views/shared/.*\.erb$'), ['CONTROLLERS']],    
    
    #change to app/helpers/application -- rerun all controller tests
    [re.compile('^/app/helpers/application_helper\.rb$'), ['CONTROLLERS']],

    #change to config/routes.rb -- rerun all controllers
    [re.compile('^/config/routes\.rb$'), ['CONTROLLERS']],
    
    #change to config/*.rb !routes -- rerun everything
    [re.compile('^/config/.*\.rb$'), ['ALL']],

    #change to config/database.yml -- rerun everything
    [re.compile('^/config/database\.yml$'), ['ALL']],
    
    #change to config/locales/*/*.yml -- rerun controllers
    [re.compile('^/config/locales/.*\.yml$'), ['CONTROLLERS']],

    #change to any test in test directory
    [re.compile('^/test/.*_test\.rb$'),['SELF']],
    
    #change to helper in test top directory
    [re.compile('^/test/\w+_helper\.rb$'),['ALL']],
    
    #change to test/fixtures/[name] -- rerun controller and model test
    [re.compile('^/test/fixtures/(?P<plural>.*)\.yml$'),
            ['/test/controllers/PLURAL_controller_test.rb',
            '/test/models/SINGULAR_test.rb']],
            
    #change to app/models/[name] -- rerun model test
    [re.compile('^/app/models/(?P<singular>.*)\.rb$'),
            ['/test/models/SINGULAR_test.rb']],
    
    #change to app/controllers/[name] -- rerun controller test
    [re.compile('^/app/controllers/(?P<plural>.*)\_controller\.rb$'),
            ['/test/controllers/PLURAL_controller_test.rb']],
            
    #change to app/views/[name]/any -- rerun controller test
    [re.compile('^/app/views/(?P<plural>.*?)/.*\.erb$'),
            ['/test/controllers/PLURAL_controller_test.rb']],
            
    #change to app/helpers/[name] -- rerun controller test
    [re.compile('^/app/helpers/(?P<plural>.*)\_helper\.rb$'),
            ['/test/controllers/PLURAL_controller_test.rb']],
            
    #change to app/mailers/[name] -- rerun mailer test
    [re.compile('^/app/mailers/(?P<singular>.*)\_mailer\.rb$'),
            ['/test/mailers/SINGULAR_mailer_test.rb']],
    
    #change to /db/*.rb send notice to consider rake db:test:prepare
    [re.compile('^/db/.*\.rb$'),['DBCHANGE']]
    ]
    
testtypepatterns = {
    'CONTROLLERS': re.compile('^/test/.*controller_test\.rb$'),
    'MODELS': re.compile('^/test/(units|models)/.*_test\.rb$'),
    'MAILERS': re.compile('^/test/.*_mailer_test\.rb$'),
    'ALL': None
}



   
#### MODULE VARS
watchfiles = {}
teststorun = {}
skippedfiles = []
missingtestforfiles = []
missingtests = []
ngprocess = {'popen':None,'pid':None}

### CONSTANTS
CLEAR_TERM = "\033\143"   # clear the terminal

### FUNCTIONS
def pdebug( string ):
    if DEBUG:
        print(string)
        
def pverbose( string ):
    if VERBOSE:
        print(string)
        
### NAILGUN SERVER FUNCTIONS
def print_nailgun_output(nailgun):
    if nailgun['popen'] is not None:
        stdout, stderr = nailgun['popen'].communicate()
        if stdout is not None:
            pdebug("NGServer stdout:\t" + str(stdout))
        if stderr is not None:
            pdebug("NGServer stderr:\t" + str(stderr))
        else:
            pdebug("NGServer: no output available")   
            
     
def manage_nailgun(nailgun) :
    if nailgun['popen'] is None:
        if nailgun['pid'] is not None:
            print("\nNGServer unexpected result:not popen and with pid")
            if check_pid(nailgun['pid']):
                os.kill(nailgun['pid'])
            nailgun['pid'] = None
        sys.stdout.write("\nNGServer restarting...")
        nailgun['popen'] = subprocess.Popen(start_nailgun_server_command, 
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        shell=True)
        nailgun['pid'] = nailgun['popen'].pid
        time.sleep(wait_after_ng_restart)
        print("done")
        #note that CTRL-C will be sent to this process unless set up new input           
    retcode = nailgun['popen'].poll() #returns None while subprocess is running
    if retcode is not None:
        print("\nNGServer unexpected result on start. Attempting restart")
        shutdown_nailgun(nailgun)
        return nailgun
    return nailgun
        
def shutdown_nailgun(nailgun):
    pdebug("Attempting NGServer shutdown")
    if check_pid(nailgun['pid']):
        if nailgun['popen'] is not None:
            
            pdebug("\nSending CTRL-C to Nailgun server")
            nailgun['popen'].send_signal(signal.SIGINT)
            nailgun['pid'] = None
            print_nailgun_output(nailgun)
        else:
            if nailgun['pid'] is not None:
                pdebug("\nNGServer unexpected result:not popen and with pid")
                if check_pid(nailgun['pid']):
                    os.kill(nailgun['pid'])
                nailgun['pid'] = None
    else:
        pdebug("pid not running...setting popen,pid to None")
        nailgun['popen'] = None
        nailgun['pid'] = None
    return nailgun
    
def check_pid(pid):        
    """ Check For the existence of a unix pid. """
    try:
        os.kill(pid, 0)
    except OSError:
        pdebug("pid not running" + str(pid))
        return False
    else:
        pdebug("pid running" + str(pid))
        return True
    
def runProcess(exe):    
    p = subprocess.Popen(exe, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    while(True):
        retcode = p.poll() #returns None while subprocess is running
        line = p.stdout.readline()
        yield line
        if(retcode is not None):
            break
    
### TEST DISCOVERY AND MANAGEMENT FUNCTIONS    
def set_tests_dirty( tests ):
    """Given array of tests set the teststorun entries to true"""
    for test in tests:
        if test in testtypepatterns.keys():
            set_type_tests_dirty( test )
        else:
            teststorun[test] = True
    return
    
def set_type_tests_dirty( type ):
    """Given type of tests set the teststorun relevant entries to true"""
    if type == 'ALL':
        for test in teststorun.keys():
            teststorun[test] = True
    else:
        for test in teststorun.keys():
            m = testtypepatterns[type].match(test)
            if m:
                teststorun[test] = True
    return

def check_new_file( fpath ):
    """Given path to file, check for applicable tests and track in watchfiles"""
    #see if it is a file that matches the first filter of files to consider
    result = ptrn_first_filter.match(fpath)
    if result is None:
        #keep list of skipped files and add this fpath to list so don't recheck
        #don't add list of .git|tmp files to skipped files array
        sf = ptrn_second_filter.match(fpath)  
        if sf is None:
            skippedfiles.append(fpath)
        watchfiles[fpath] = False 
    else:
        for pattern, test_templates in testpatterns:
            #see which pattern matches this file
            m = pattern.match(fpath)
            
            if m:
                #use regex pattern to determine model name
                gdict = m.groupdict()
                if gdict is not None:
                    model_singular = gdict.get('singular')
                    model_plural = gdict.get('plural')
                # build array of tests for this file
                tests = []
                for template in test_templates:
                    if template == 'IGNORE':
                        skippedfiles.append(fpath)
                        watchfiles[fpath] = False
                        return
                    elif template == 'DBCHANGE':
                        print("DB File:\t" + fpath + " consider rake db:test:prepare")
                        skippedfiles.append(fpath)
                        watchfiles[fpath] = False
                        return                    
                    elif template == 'SELF':
                        tests.append(m.group(0))
                    elif template in testtypepatterns.keys():
                        #if group of tests (ALL, CONTROLLERS, MODELS, etc) then store that
                        # and catch in the set_dirty_tests
                        tests.append(template)
                    elif model_singular is not None:
                        template = template.replace('SINGULAR',model_singular)
                        #figure out plural
                        if model_singular[-1:] == 'y':
                            model_plural = model_singular[:-1] + 'ies'
                        else:
                            model_plural = model_singular + 's'
                        #replace PLURAL with plural
                        tests.append(template.replace('PLURAL',model_plural))
                    elif model_plural is not None:
                        #~ template = template
                        template = template.replace('PLURAL',model_plural)
                        #figure out singular
                        if model_plural[-3:] == 'ies':
                            model_singular = model_plural[:-3] + 'y'
                        else:
                            model_singular = model_plural[:-1]
                        #replace SINGULAR with singular
                        tests.append(template.replace('SINGULAR',model_singular))
                    else:
                        print("Group no match:\t" + fpath + template)
                        missingtestfiles.append(fpath)
                        watchfiles[fpath] = False
                        return
                set_tests_dirty( tests )
                print("Tracking:\t" + fpath)
                mtime = os.path.getmtime(cwd + fpath)
                watchfiles[fpath] = {'mtime' : mtime, 'tests' : tests}
                return  #only going to find one match in the testpatterns array
        #no pattern matched -- add to watchfiles so we don't keep trying
        print("Missing Test:\t" + fpath)
        missingtestforfiles.append(fpath)
        watchfiles[fpath] = False
    return

def check_for_update( fpath ):
    """Given path to file, check to see if the modified time has changed, if so run tests"""
    if os.path.isfile(cwd + fpath):
        mtime = os.path.getmtime(cwd + fpath)
    else:
        mtime = 0
    if mtime != watchfiles[fpath]['mtime']:
        print("File updated:\t" + fpath)
        watchfiles[fpath]['mtime'] = mtime
        set_tests_dirty(watchfiles[fpath]['tests'])
    return
    
def scan_cwd():
    """scan the working directory for files that need to be tested"""
    for root, dirs, files in os.walk(cwd):
        for name in files:
            fpath = os.path.join(root, name).replace(cwd,"")
            if fpath not in watchfiles:   
                check_new_file(fpath)
            elif watchfiles[fpath] is False:
                continue #skip!
            else:
                check_for_update(fpath)
    return
                
def scan_watchfiles():
    for fpath, check in watchfiles.items():
        if check : check_for_update(fpath)
    return
            
def print_test_output(raw):
    print("\nResults " + "-" * 40 )
    lines = raw.splitlines()
    for line in lines:
        bShow = True
        if FULLTRACE is False:
            for pattern in tracesilencers:
                if pattern.match(line):
                    bShow = False
                    break
        if bShow:
            print(line)
        
        
def run_dirty_tests():
    """run tests that have dirty set to true"""
    if True in teststorun.values():
        if DEBUG is False:
            print(CLEAR_TERM)
        else:
            print()
        print("---- " + time.asctime(time.localtime()) + "-" * 40)
        for file in missingtestforfiles: print("Missing test for file:" + file) 
        tests_arg = '"%w['
        nTests = 0
        #build list of tests to run, check status as we go
        for test, dirty in teststorun.items():
            if dirty is False:
                continue
            else:
                if os.path.isfile(cwd + test):
                    nTests = nTests+1
                    tests_arg = tests_arg + " " + test
                else:
                    print("Missing a test file: " + test)
                    missingtests.append(test)
                teststorun[test] = False
        tests_arg = tests_arg + '].each { |f| require f }"'
        if nTests:
            #~ print(subprocess.getoutput('jruby -e "puts \'hello\'"'))
            args = ['jruby', '--ng','-I.:lib:test','-rubygems','-e',tests_arg]
            cmd = " ".join(args)
            pverbose("Running:\t" + cmd)
            print("\nSubmitted " + str(nTests) + " test files ----- The Oxen is slow but the Earth is patient " + "-" * 5)
            output =  subprocess.getoutput(cmd)
            print_test_output(output)            
    return

### MAIN
for arg in sys.argv:
    #~ print("Arg:" + arg )
    if arg[2:] == 'debug':
        print("Debug mode on, verbose and full-trace also on")
        DEBUG = True
        VERBOSE = True
        FULLTRACE = True
    elif arg[2:] == 'full-trace':
        print("Full traceback (silencers turned off)")
        FULLTRACE = True
    if arg[2:] == 'verbose':
        print("Verbose mode on")
        VERBOSE = True
    elif arg[2:] == 'help' or arg[1:] == 'h':
        print("usage: pytest.py [--debug --full-trace --verbose -h --help]")
        exit()
i = 0
while (True):
    try:
        ngprocess = manage_nailgun(ngprocess)
        sys.stdout.write("\rdone ng manage, full scan in " + str(i))
        if i < 1:
            scan_cwd()
            i = num_loops_between_scan_cwd
        else:
            scan_watchfiles()
        run_dirty_tests()
        time.sleep(secs_between_loops)
        i=i-1

    except KeyboardInterrupt:
        print("\nInterrupt again to really quit.")
        try:
            #ctrl-c is passed to ng process -- which cause it to halt. so restart it
            ngprocess = manage_nailgun(ngprocess)
            time.sleep(wait_after_ng_restart)
            start = time.time()
            print("Forcing scan and run...")
            while ( time.time() - start < 10 ):
                scan_cwd()
                set_type_tests_dirty( 'ALL' )
                run_dirty_tests()
                time.sleep(wait_after_ng_restart)
            continue
        except KeyboardInterrupt:
            print('Done test monitoring.')
            ngprocess = shutdown_nailgun(ngprocess)
            #~ STATS_FILE.close()
            break
            
pverbose("\nCurrent directory:\t" + cwd +"-"*20+"\n")
pdebug("\nTests:" + str(len(teststorun)) +"-"*20+"\n")
for file in teststorun: pdebug(file) 
pdebug("\nSkipped files:\t" + str(len(skippedfiles)) +"-"*20+"\n") 
for file in skippedfiles: pdebug(file) 
pdebug("\nMissing a test for files:\t" + str(len(missingtestforfiles))+"-"*20+"\n")
for file in missingtestforfiles: pdebug(file) 
pdebug("\nMissing test files:\t" + str(len(missingtests))+"-"*20+"\n") 
for file in missingtests: pdebug(file) 



  
#jruby -I.:lib:test -rubygems -e "%w[test/unit test/controllers/deals_controller_test.rb test/controllers/sessions_controller_test.rb test/helpers/travel_helper_test.rb test/controllers/hotels_controller_test.rb test/controllers/destinations_controller_test.rb test/controllers/difficulties_controller_test.rb test/controllers/testimonials_controller_test.rb test/helpers/blurbs_helper_test.rb test/models/area_test.rb test/helpers/difficulties_helper_test.rb test/models/tab_test.rb test/controllers/tours_controller_test.rb test/controllers/tabs_controller_test.rb test/controllers/leads_controller_test.rb test/controllers/areas_controller_test.rb test/controllers/articles_controller_test.rb test/controllers/blurbs_controller_test.rb test/models/tour_test.rb test/models/blurb_test.rb test/helpers/articles_helper_test.rb test/helpers/categories_helper_test.rb test/mailers/lead_mailer_test.rb test/controllers/admin_controller_test.rb test/helpers/tours_helper_test.rb test/models/difficulty_test.rb test/helpers/admin_helper_test.rb test/helpers/deals_helper_test.rb test/helpers/testimonials_helper_test.rb test/helpers/hotels_helper_test.rb test/models/testimonial_test.rb test/controllers/travel_controller_test.rb test/helpers/destinations_helper_test.rb test/controllers/categories_controller_test.rb test/models/category_test.rb test/models/destination_test.rb test/helpers/sessions_helper_test.rb test/helpers/leads_helper_test.rb test/models/version_test.rb test/models/deal_test.rb test/models/hotel_test.rb test/helpers/tabs_helper_test.rb test/helpers/areas_helper_test.rb test/models/article_test.rb test/models/lead_test.rb].each { |f| require f }"  
