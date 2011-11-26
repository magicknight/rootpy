import ROOT
import time
import os
import sys
import multiprocessing
from multiprocessing import Process
from operator import add, itemgetter
import uuid
from ..tree.filtering import *
from .. import common
from . import multilogging
import logging
import traceback
import shutil
import subprocess
import signal
from .student import Student
try:
    import cPickle as pickle
except:
    import pickle


NCPUS = multiprocessing.cpu_count()


class Supervisor(Process):

    def __init__(self, student, outputname,
                 files,
                 metadata=None,
                 nstudents=NCPUS,
                 connect_queue=None,
                 gridmode=False,
                 queuemode=True,
                 nice=0,
                 args=None,
                 **kwargs):
                
        Process.__init__(self)
        
        self.process = student
        if isinstance(student, basestring):
            # remove .py extension if present
            student = os.path.splitext(student)[0]
            print "importing %s..." % student
            exec "from %s import %s" % (student, student)
            self.process = eval(student)
        if not issubclass(self.process, Student):
            raise TypeError("%s must be a subclass of Student" % student)
        
        self.name = self.process.__name__
        self.files = files[:]
        self.fileset = metadata
        self.outputname = outputname
        self.gridmode = gridmode
        self.nice = nice
        if self.gridmode:
            self.nstudents = 1
            queuemode = False
        else:
            self.nstudents = min(nstudents, len(self.files))
        self.queuemode = queuemode
        self.student_outputs = []
        self.kwargs = kwargs
        self.logger = None
        self.args = args
        self.connect_queue = connect_queue
        
    def run(self):
        
        # ignore sigterm signal and let parent take care of this
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        ROOT.gROOT.SetBatch()
        
        # logging
        self.logging_queue = multiprocessing.Queue(-1)
        self.listener = multilogging.Listener("supervisor-%s-%s.log" % \
            (self.name, self.outputname), self.logging_queue)
        self.listener.start()
        
        h = multilogging.QueueHandler(self.logging_queue)
        self.logger = logging.getLogger("Supervisor")
        self.logger.addHandler(h)
        self.logger.setLevel(logging.DEBUG)
        
        if not self.gridmode:
            sys.stdout = multilogging.stdout(self.logger)
            sys.stderr = multilogging.stderr(self.logger)
        
        if self.queuemode:
            self.file_queue = multiprocessing.Queue(self.nstudents * 2)
        self.output_queue = multiprocessing.Queue(-1)
        try:
            print "Will run on %i file(s):" % len(self.fileset.files)
            for filename in self.fileset.files:
                print "%s" % filename
            sys.stdout.flush()
            self.hire_students()
            self.supervise()
            self.publish()
        except:
            print sys.exc_info()
            traceback.print_tb(sys.exc_info()[2])
        
        print "Done"
        if self.queuemode:
            self.file_queue.close()
        self.output_queue.close()
        self.logging_queue.put(None)
        self.listener.join()

    def hire_students(self):
        
        if self.queuemode:
            students = [
                self.process(
                    name = self.name,
                    files = self.file_queue,
                    output_queue = self.output_queue,
                    logging_queue = self.logging_queue,
                    gridmode = self.gridmode,
                    metadata = self.fileset,
                    nice = self.nice,
                    args = self.args,
                    **self.kwargs
                ) for i in xrange(self.nstudents) ]
        else:
            # deal out files
            filesets = [[] for i in xrange(self.nstudents)]
            while len(self.files) > 0:
                for fileset in filesets:
                    if len(self.files) > 0:
                        fileset.append(self.files.pop(0))
                    else:
                        break
            students = [
                self.process(
                    name = self.name,
                    files = fileset,
                    output_queue = self.output_queue,
                    logging_queue = self.logging_queue,
                    gridmode = self.gridmode,
                    metadata = self.fileset,
                    nice = self.nice,
                    args = self.args,
                    **self.kwargs
                ) for fileset in filesets ]
        self.process_table = dict([(p.uuid, p) for p in students])
            
    def supervise(self):
        
        if self.queuemode:
            # fill queue
            while self.files and not self.file_queue.full():
                self.file_queue.put(self.files.pop())
        for student in self.process_table.values():
            student.start()
        while self.process_table:
            if self.connect_queue is not None:
                if not self.connect_queue.empty():
                    msg = self.connect_queue.get()
                    if msg is None:
                        print "%s will now terminate..." % self.__class__.__name__
                        for student in self.process_table.values():
                            student.terminate()
                        return
            if self.queuemode:
                # fill queue
                while self.files and not self.file_queue.full():
                    self.file_queue.put(self.files.pop())
                if not self.files:
                    for i in xrange(self.nstudents):
                        self.file_queue.put(None)
            while not self.output_queue.empty():
                id, output = self.output_queue.get()
                process = self.process_table[id]
                process.join()
                del self.process_table[id]
                if output is not None and process.exitcode == 0:
                    self.student_outputs.append(output)
            time.sleep(1)
    
    def publish(self, merge=True, weight=False):
        
        if len(self.student_outputs) > 0:
            outputs = []
            event_filters = []
            object_filters = []
            for event_filter, object_filter, output in self.student_outputs:
                event_filters.append(event_filter)
                object_filters.append(object_filter)
                outputs.append(output)
            
            print "\n===== Cut-flow of event filters for dataset %s: ====\n"% self.outputname
            totalEvents = 0
            combinedEventFilterlist = reduce(FilterList.merge, event_filters)
            combinedObjectFilterlist = reduce(FilterList.merge, object_filters)
            totalEvents = combinedEventFilterlist.total
            print "Event Filters:\n%s"% combinedEventFilterlist
            print "Object Filters:\n%s"% combinedObjectFilterlist
            
            with open("cutflow.p",'w') as pfile:
                pickle.dump({"event": combinedEventFilterlist.basic(),
                             "object": combinedObjectFilterlist.basic()}, pfile)
            
            if merge:
                outputname = "%s.root" % self.outputname 
                if os.path.exists(outputname):
                    os.unlink(outputname)
                if len(outputs) == 1:
                    shutil.move(outputs[0], outputname)
                else:
                    subprocess.call(["hadd", outputname] + outputs)
                    for output in outputs:
                        os.unlink(output)
            
            # set weights:
            """
            if in gridmode, set weights offline after downloading
            and hadding all output
            """
            if totalEvents != 0 and weight and not self.gridmode:
                outfile = ROOT.TFile.Open("%s.root"% self.outputname, "update")
                trees = common.getTrees(outfile)
                for tree in trees:
                    tree.SetWeight(self.fileset.weight/totalEvents)
                    tree.Write("", ROOT.TObject.kOverwrite)
                outfile.Close()
