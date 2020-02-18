
__all__ = ["TaskParser"]

from Gaugi.messenger import LoggingLevel, Logger
from Gaugi.messenger.macros import *
from Gaugi import csvStr2List, expandFolders
from Gaugi import load

# Connect to DB
from orchestra.constants import CLUSTER_VOLUME
from orchestra.db import OrchestraDB
from orchestra.db import Task,Dataset,File, Board, Job
from orchestra import Status, Cluster


# common imports
import glob
import numpy as np
import argparse
import sys,os
import hashlib
import argparse



class TaskParser(Logger):


  def __init__(self , db, args=None):

    Logger.__init__(self)
    self.__db = db

    if args:
      # Create Task
      create_parser = argparse.ArgumentParser(description = '', add_help = False)
      create_parser.add_argument('-c','--configFile', action='store',
                          dest='configFile', required = True,
                          help = "The job config file that will be used to configure the job (sort and init).")
      create_parser.add_argument('-d','--dataFile', action='store',
                          dest='dataFile', required = True,
                          help = "The data/target file used to train the model.")
      create_parser.add_argument('--exec', action='store', dest='execCommand', required=True,
                          help = "The exec command")
      create_parser.add_argument('--containerImage', action='store', dest='containerImage', required=True,
                          help = "The container image point to docker hub. The container must be public.")
      create_parser.add_argument('-t','--task', action='store', dest='taskname', required=True,
                          help = "The task name to be append into the db.")
      create_parser.add_argument('--sd','--secondaryDS', action='store', dest='secondaryDS', required=False,  default="{}",
                          help = "The secondary datasets to be append in the --exec command. This should be:" +
                          "--secondaryData='{'REF':'path/to/my/extra/data',...}'")
      create_parser.add_argument('--gpu', action='store_true', dest='gpu', required=False, default=False,
                          help = "Send these jobs to GPU slots")
      create_parser.add_argument('--et', action='store', dest='et', required=False,default=None,
                          help = "The ET region (ringer staff)")
      create_parser.add_argument('--eta', action='store', dest='eta', required=False,default=None,
                          help = "The ETA region (ringer staff)")
      create_parser.add_argument('--dry_run', action='store_true', dest='dry_run', required=False, default=False,
                          help = "Use this as debugger.")
      create_parser.add_argument('--bypass', action='store_true', dest='bypass_test_job', required=False, default=False,
                          help = "Bypass the job test.")


      retry_parser = argparse.ArgumentParser(description = '', add_help = False)
      retry_parser.add_argument('-t','--task', action='store', dest='taskname', required=True,
                    help = "The task name to be retry")
      delete_parser = argparse.ArgumentParser(description = '', add_help = False)
      delete_parser.add_argument('-t','--task', action='store', dest='taskname', required=True,
                    help = "The task name to be remove")
      list_parser = argparse.ArgumentParser(description = '', add_help = False)
      list_parser.add_argument('-u','--user', action='store', dest='username', required=True,
                    help = "The username.")

      kill_parser = argparse.ArgumentParser(description = '', add_help = False)
      kill_parser.add_argument('-u','--user', action='store', dest='username', required=True,
                    help = "The username.")
      kill_parser.add_argument('-t','--task', action='store', dest='taskname', required=False,
                    help = "The taskname to be killed.")
      kill_parser.add_argument('-a','--all', action='store_true', dest='kill_all', required=False, default=False,
                    help = "Remove all tasks.")






      parent = argparse.ArgumentParser(description = '', add_help = False)
      subparser = parent.add_subparsers(dest='option')

      # Datasets
      subparser.add_parser('create', parents=[create_parser])
      subparser.add_parser('retry', parents=[retry_parser])
      subparser.add_parser('delete', parents=[delete_parser])
      subparser.add_parser('list', parents=[list_parser])
      subparser.add_parser('kill', parents=[kill_parser])
      args.add_parser( 'task', parents=[parent] )


  def compile( self, args ):
    # Task CLI
    if args.mode == 'task':
      if args.option == 'create':
        self.create(args.taskname, args.dataFile, args.configFile, args.secondaryDS,
                    args.execCommand,args.containerImage,args.et,args.eta,args.gpu,
                    args.bypass_test_job, args.dry_run)
      elif args.option == 'retry':
        self.retry(args.taskname)
      elif args.option == 'delete':
        self.delete(args.taskname)
      elif args.option == 'list':
        self.list(args.username)
      elif args.option == 'kill':
        self.kill(args.username, 'all' if args.kill_all else args.taskname)






  def create( self, taskname, dataFile,
                    configFile, secondaryDS,
                    execCommand, containerImage, et=None, eta=None, gpu=False,
                    bypass_test_job=False, dry_run=False):


    # check task policy (user.username)
    if taskname.split('.')[0] != 'user':
      MSG_FATAL( self, 'The task name must starts with: user.%USER.taskname.')

    # check task policy (username must exist into the database)
    username = taskname.split('.')[1]

    if username in self.__db.getAllUsers():
      MSG_FATAL( self, 'The username does not exist into the database. Please, report this to the db manager...')


    # Check if the task exist into the databse
    if self.__db.getUser(username).getTask(taskname) is not None:
      MSG_FATAL( self, "The task exist into the database. Abort.")


    # check data (file) is in database
    if self.__db.getDataset(username, dataFile) is None:
      MSG_FATAL( self, "The file (%s) does not exist into the database. Should be registry first.", dataFile)


    # check configFile (file) is in database
    if self.__db.getDataset(username, configFile) is None:
      MSG_FATAL( self, "The config file (%s) does not exist into the database. Should be registry first.", configFile)


    # Get the secondary data as dict
    secondaryDS = eval(secondaryDS)


    # check secondary data paths exist is in database
    for key in secondaryDS.keys():
      if self.__db.getDataset(username, secondaryDS[key]) is None:
        MSG_FATAL( self, "The secondary data file (%s) does not exist into the database. Should be registry first.", secondaryDS[key])




    # check exec command policy
    if not '%DATA' in execCommand:
      MSG_FATAL( self,  "The exec command must include '%DATA' into the string. This will substitute to the dataFile when start.")
    if not '%IN' in execCommand:
      MSG_FATAL( self, "The exec command must include '%IN' into the string. This will substitute to the configFile when start.")
    if not '%OUT' in execCommand:
      MSG_FATAL( self, "The exec command must include '%OUT' into the string. This will substitute to the outputFile when start.")


    # parser the secondary data in the exec command
    for key in secondaryDS.keys():
      if not key in execCommand:
        MSG_FATAL( selrf, "The exec command must include %s into the string. This will substitute to %s when start",key, secondaryDS[key])



    # check if the output exist into the dataset base
    if self.__db.getDataset(username, taskname ):
      MSG_FATAL( self, "The output dataset exist. Please, remove than or choose another name for this task", taskname )


    # Check if the board monitoring for this task exist into the database
    if self.__db.session().query(Board).filter( Board.taskName==taskname ).first():
      MSG_FATAL( self, "There is a board monitoring with this taskname. Contact the administrator." )


    # check if task exist into the storage
    outputFile = CLUSTER_VOLUME +'/'+username+'/'+taskname

    if os.path.exists(outputFile):
      MSG_WARNING(self, "The task dir exist into the storage. Beware!")
    else:
      # create the task dir
      MSG_INFO(self, "Creating the task dir in %s", outputFile)
      os.system( 'mkdir %s '%(outputFile) )


    # create the task into the database
    if not dry_run:
      try:
        user = self.__db.getUser( username )
        task = self.__db.createTask( user, taskname, configFile, dataFile, taskname,
                            containerImage, self.__db.getCluster(),
                            secondaryDataPath=secondaryDS,
                            templateExecArgs=execCommand,
                            etBinIdx=et,
                            etaBinIdx=eta,
                            isGPU=gpu,
                            )
        task.setStatus('hold')

        configFiles = self.__db.getDataset(username, configFile).getAllFiles()

        _dataFile = self.__db.getDataset(username, dataFile).getAllFiles()[0].getPath()
        _dataFile = _dataFile.replace( CLUSTER_VOLUME, '/volume' ) # to docker path
        _outputFile = '/volume/'+username+'/'+taskname # to docker path
        _secondaryDS = {}

        for key in secondaryDS.keys():
          _secondaryDS[key] = self.__db.getDataset(username, secondaryDS[key]).getAllFiles()[0].getPath()
          _secondaryDS[key] = _secondaryDS[key].replace(CLUSTER_VOLUME, '/volume') # to docker path

        for idx, file in enumerate(configFiles):

          _configFile = file.getPath()
          _configFile = _configFile.replace(CLUSTER_VOLUME, '/volume') # to docker path

          command = execCommand
          command = command.replace( '%DATA' , _dataFile  )
          command = command.replace( '%IN'   , _configFile)
          command = command.replace( '%OUT'  , _outputFile)

          for key in _secondaryDS:
            command = command.replace( key  , _secondaryDS[key])

          job = self.__db.createJob( task, _configFile, idx, execArgs=command, isGPU=gpu, priority=-1 )
          job.setStatus('assigned' if bypass_test_job else 'registered')


        ds  = Dataset( username=username, dataset=taskname, cluster=self.__db.getCluster(), task_usage=True)
        ds.addFile( File(path=outputFile, hash='' ) ) # the real path
        self.__db.createDataset(ds)
        self.createBoard( user, task )
        task.setStatus('registered')
        self.__db.commit()
      except Exception as e:
        MSG_FATAL(self, e)





  def delete( self, taskname ):

    if taskname.split('.')[0] != 'user':
      MSG_FATAL( self, 'The task name must starts with: user.%USER.taskname.')
    username = taskname.split('.')[1]
    if username in self.__db.getAllUsers():
      MSG_FATAL( self, 'The username does not exist into the database. Please, report this to the db manager...')

    try:
      user = self.__db.getUser( username )
    except:
      MSG_FATAL( self , "The user name (%s) does not exist into the data base", username)

    try:
      task = self.__db.getTask( taskname )
    except:
      MSG_FATAL( self, "The task name (%s) does not exist into the data base", args.taskname)

    id = task.id

    try:
      self.__db.session().query(Job).filter(Job.taskId==id).delete()
    except Exception as e:
      MSG_FATAL( self, "Impossible to remove Job lines from (%d) task", id)

    try:
      self.__db.session().query(Task).filter(Task.id==id).delete()
    except Exception as e:
      MSG_FATAL( self, "Impossible to remove Task lines from (%d) task", id)

    try:
      self.__db.session().query(Board).filter(Board.taskId==id).delete()
    except Exception as e:
      MSG_WARNING( self, "Impossible to remove Task board lines from (%d) task", id)


    # prepare to remove from database
    ds = self.__db.getDataset( username, taskname )

    if not ds.task_usage:
      MSG_FATAL( self, "This dataset is not task usage. There is something strange..." )

    for file in ds.getAllFiles():
      # Delete the file inside of the dataset
      self.__db.session().query(File).filter( File.id==file.id ).delete()

    # Delete the dataset
    self.__db.session().query(Dataset).filter( Dataset.id==ds.id ).delete()

    # The path to the dataset in the cluster
    file_dir = CLUSTER_VOLUME + '/' + username + '/' + taskname
    file_dir = file_dir.replace('//','/')

    # Delete the file from the storage
    # check if this path exist
    if os.path.exists(file_dir):
      command = 'rm -rf {FILE}'.format(FILE=file_dir)
      print(command)
    else:
      MSG_WARNING(self, "This dataset does not exist into the database (%s)", file_dir)

    self.__db.commit()







  def retry( self, taskname ):

    if taskname.split('.')[0] != 'user':
      MSG_FATAL( self, 'The task name must starts with: user.%USER.taskname.')
    username = taskname.split('.')[1]
    if username in self.__db.getAllUsers():
      MSG_FATAL( self, 'The username does not exist into the database. Please, report this to the db manager...')
    try:
      user = self.__db.getUser( username )
    except:
      MSG_FATAL( self , "The user name (%s) does not exist into the data base", username)

    try:
      task = self.__db.getTask( taskname )
      for job in task.getAllJobs():
        print(job)
        if ( (job.getStatus() == Status.FAILED) or (job.getStatus() == Status.KILL) or \
            (job.getStatus() == Status.KILLED) or (job.getStatus() == Status.BROKEN) ):

          job.setStatus(Status.REGISTERED)
      task.setStatus(Status.REGISTERED)

    except:
      MSG_FATAL( self, "The task name (%s) does not exist into the data base", args.taskname)

    self.__db.commit()



  def list( self, username ):

    if username in self.__db.getAllUsers():
      MSG_FATAL( self, 'The username does not exist into the database. Please, report this to the db manager...')


    from Gaugi import Color
    def getStatus(status):
      if status == 'registered':
        return Color.CWHITE2+"REGISTERED"+Color.CEND
      elif status == 'assigned':
        return Color.CWHITE2+"ASSIGNED"+Color.CEND
      elif status == 'testing':
        return Color.CGREEN2+"TESTING"+Color.CEND
      elif status == 'running':
        return Color.CGREEN2+"RUNNING"+Color.CEND
      elif status == 'done':
        return Color.CGREEN2+"DONE"+Color.CEND
      elif status == 'failed':
        return Color.CGREEN2+"DONE"+Color.CEND
      elif status == 'killed':
        return Color.CRED2+"KILLED"+Color.CEND
      elif status == 'finalized':
        return Color.CRED2+"FINALIZED"+Color.CEND
      elif status == 'hold':
        return Color.CRED2+"HOLD"+Color.CEND


    from prettytable import PrettyTable
    t = PrettyTable([ Color.CGREEN2 + 'Username' + Color.CEND,
                      Color.CGREEN2 + 'Taskname' + Color.CEND,
                      Color.CGREEN2 + 'Assigned' + Color.CEND,
                      Color.CGREEN2 + 'Testing'  + Color.CEND,
                      Color.CGREEN2 + 'Running'  + Color.CEND,
                      Color.CRED2   + 'Failed'   + Color.CEND,
                      Color.CGREEN2 + 'Done'     + Color.CEND,
                      Color.CRED2   + 'killed'   + Color.CEND,
                      Color.CGREEN2 + 'Status'   + Color.CEND,
                      ])

    tasks = self.__db.session().query(Board).filter( Board.username==username ).all()
    # Loop over all datasets inside of the username
    for b in tasks:
      t.add_row(  [username, b.taskName, b.assigned, b.testing, b.running, b.failed,  b.done, b.killed, getStatus(b.status)] )
    print(t)



  def kill( self, username, taskname ):

    try:
      user = self.__db.getUser( username )
    except:
      MSG_FATAL( self , "The user name (%s) does not exist into the data base", username)

    if taskname=='all':
      MSG_INFO( self, "Remove all tasks inside of %s username", username )
      for task in user.getAllTasks():
        for job in task.getAllJobs():
          job.setStatus(Status.KILLED if job.getStatus() is Status.ASSIGNED else Status.KILL)
    else:
      if taskname.split('.')[0] != 'user':
        MSG_FATAL( self, 'The task name must starts with: user.%USER.taskname.')
      try:
        task = self.__db.getTask( taskname )
        for job in task.getAllJobs():
          if job.getStatus()==Status.RUNNING or job.getStatus()==Status.TESTING:
            # Remove all jobs from the slot
            job.setStatus(Status.KILL)
          else:
            job.setStatus(Status.KILLED)
      except:
        MSG_FATAL( self, "The task name (%s) does not exist into the data base", args.taskname)

    self.__db.commit()




  #
  # This is for monitoring purpose. Should be used to dashboard view
  #
  def createBoard( self , user, task):

    board = Board( username=user.username, taskId=task.id, taskName=task.taskName )
    board.jobs = len(task.getAllJobs())
    board.registered = board.jobs
    board.assigned=board.testing=board.running=board.failed=board.done=board.killed=0
    board.status = task.status
    self.__db.session().add(board)








