#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 2010-2011 Nico Schottelius (nico-cdist at schottelius.org)
#
# This file is part of cdist.
#
# cdist is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# cdist is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with cdist. If not, see <http://www.gnu.org/licenses/>.
#
#

import datetime
import logging
import os
import stat

log = logging.getLogger(__name__)

import cdist.path

CODE_HEADER                     = "#!/bin/sh -e\n"

class Config:
    """Cdist main class to hold arbitrary data"""

    def __init__(self, target_host, 
                    initial_manifest=False, remote_user="root",
                    home=None, debug=False):

        self.target_host = target_host
        self.debug = debug
        self.remote_user = remote_user
        self.remote_prefix = ["ssh", self.remote_user + "@" + self.target_host]

        self.path = cdist.path.Path(self.target_host, 
                        initial_manifest=initial_manifest,
                        remote_user=self.remote_user,
                        remote_prefix=self.remote_prefix,
                        base_dir=home,
                        debug=debug)
        
        self.objects_prepared = []

    def cleanup(self):
        self.path.cleanup()

    def run_global_explores(self):
        """Run global explorers"""
        explorers = self.path.list_global_explorers()
        if(len(explorers) == 0):
            raise CdistError("No explorers found in", self.path.global_explorer_dir)

        self.path.transfer_global_explorers()
        for explorer in explorers:
            output = self.path.global_explorer_output_path(explorer)
            output_fd = open(output, mode='w')
            cmd = []
            cmd.append("__explorer=" + cdist.path.REMOTE_GLOBAL_EXPLORER_DIR)
            cmd.append(self.path.remote_global_explorer_path(explorer))

            cdist.exec.run_or_fail(cmd, stdout=output_fd, remote_prefix=self.remote_prefix)
            output_fd.close()

    def run_type_explorer(self, cdist_object):
        """Run type specific explorers for objects"""

        type = self.path.get_type_from_object(cdist_object)
        self.path.transfer_type_explorers(type)

        cmd = []
        cmd.append("__explorer="        + cdist.path.REMOTE_GLOBAL_EXPLORER_DIR)
        cmd.append("__type_explorer="   + self.path.remote_type_explorer_dir(type))
        cmd.append("__object="          + self.path.remote_object_dir(cdist_object))
        cmd.append("__object_id="       + self.path.get_object_id_from_object(cdist_object))
        cmd.append("__object_fq="       + cdist_object)

        # Need to transfer at least the parameters for objects to be useful
        self.path.transfer_object_parameter(cdist_object)

        explorers = self.path.list_type_explorers(type)
        for explorer in explorers:
            remote_cmd = cmd + [os.path.join(self.path.remote_type_explorer_dir(type), explorer)]
            output = os.path.join(self.path.type_explorer_output_dir(cdist_object), explorer)
            output_fd = open(output, mode='w')
            log.debug("%s exploring %s using %s storing to %s", 
                        cdist_object, explorer, remote_cmd, output)
                        
            cdist.exec.run_or_fail(remote_cmd, stdout=output_fd, remote_prefix=self.remote_prefix)
            output_fd.close()

    def init_deploy(self):
        """Ensure the base directories are cleaned up"""
        log.debug("Creating clean directory structure")

        self.path.remove_remote_dir(cdist.path.REMOTE_BASE_DIR)
        self.path.remote_mkdir(cdist.path.REMOTE_BASE_DIR)

    def run_initial_manifest(self):
        """Run the initial manifest"""
        env = {  "__manifest" : self.path.manifest_dir }
        self.run_manifest(self.path.initial_manifest, extra_env=env)

    def run_type_manifest(self, cdist_object):
        """Run manifest for a specific object"""
        type = self.path.get_type_from_object(cdist_object)
        manifest = self.path.type_dir(type, "manifest")
        
        log.debug("%s: Running %s", cdist_object, manifest)
        if os.path.exists(manifest):
            env = { "__object" :    self.path.object_dir(cdist_object), 
                    "__object_id":  self.path.get_object_id_from_object(cdist_object),
                    "__object_fq":  cdist_object,
                    "__type":       self.path.type_dir(type)
                    }
            self.run_manifest(manifest, extra_env=env)

    def run_manifest(self, manifest, extra_env=None):
        """Run a manifest"""
        log.debug("Running manifest %s, env=%s", manifest, extra_env)
        env = os.environ.copy()
        env['PATH'] = self.path.bin_dir + ":" + env['PATH']

        # Information required in every manifest
        env['__target_host']            = self.target_host
        env['__global']                 = self.path.out_dir
        
        # Legacy stuff to make cdist-type-emulator work
        env['__cdist_core_dir']         = os.path.join(self.path.base_dir, "core")
        env['__cdist_local_base_dir']   = self.path.temp_dir

        # Submit information to new type emulator

        # Required for recording source
        env['__cdist_manifest']         = manifest

        # Required to find types
        env['__cdist_type_base_dir']    = self.path.type_base_dir

        # Other environment stuff
        if extra_env:
            env.update(extra_env)

        cdist.exec.shell_run_or_debug_fail(manifest, [manifest], env=env)

    def object_run(self, cdist_object, mode):
        """Run gencode or code for an object"""
        log.debug("Running %s from %s", mode, cdist_object)
        file=os.path.join(self.path.object_dir(cdist_object), "require")
        requirements = cdist.path.file_to_list(file)
        type = self.path.get_type_from_object(cdist_object)
            
        for requirement in requirements:
            log.debug("Object %s requires %s", cdist_object, requirement)
            self.object_run(requirement, mode=mode)

        #
        # Setup env Variable:
        # 
        env = os.environ.copy()
        env['__target_host']    = self.target_host
        env['__global']         = self.path.out_dir
        env["__object"]         = self.path.object_dir(cdist_object)
        env["__object_id"]      = self.path.get_object_id_from_object(cdist_object)
        env["__object_fq"]      = cdist_object
        env["__type"]           = self.path.type_dir(type)

        if mode == "gencode":
            paths = [
                self.path.type_dir(type, "gencode-local"),
                self.path.type_dir(type, "gencode-remote")
            ]
            for bin in paths:
                if os.path.isfile(bin):
                    # omit "gen" from gencode and use it for output base
                    outfile=os.path.join(self.path.object_dir(cdist_object), 
                        os.path.basename(bin)[3:])

                    outfile_fd = open(outfile, "w")

                    # Need to flush to ensure our write is done before stdout write
                    outfile_fd.write(CODE_HEADER)
                    outfile_fd.flush()

                    cdist.exec.shell_run_or_debug_fail(bin, [bin], env=env, stdout=outfile_fd)
                    outfile_fd.close()

                    status = os.stat(outfile)

                    # Remove output if empty, else make it executable
                    if status.st_size == len(CODE_HEADER):
                        os.unlink(outfile)
                    else:
                        # Add header and make executable - identically to 0o700
                        os.chmod(outfile, stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)

                        # Mark object as changed
                        open(os.path.join(self.path.object_dir(cdist_object), "changed"), "w").close()


        if mode == "code":
            local_dir   = self.path.object_dir(cdist_object)
            remote_dir  = self.path.remote_object_dir(cdist_object)

            bin = os.path.join(local_dir, "code-local")
            if os.path.isfile(bin):
                cdist.exec.run_or_fail([bin])
                

            local_remote_code = os.path.join(local_dir, "code-remote")
            remote_remote_code = os.path.join(remote_dir, "code-remote")
            if os.path.isfile(local_remote_code):
                self.path.transfer_file(local_remote_code, remote_remote_code)
                # FIXME: remote_prefix
                cdist.exec.run_or_fail([remote_remote_code], remote_prefix=self.remote_prefix)
                
    def stage_prepare(self):
        """Do everything for a deploy, minus the actual code stage"""
        self.init_deploy()
        self.run_global_explores()
        self.run_initial_manifest()
        
        old_objects = []
        objects = self.path.list_objects()

        # Continue process until no new objects are created anymore
        while old_objects != objects:
            log.debug("Prepare stage")
            old_objects = list(objects)
            for cdist_object in objects:
                if cdist_object in self.objects_prepared:
                    log.debug("Skipping rerun of object %s", cdist_object)
                    continue
                else:
                    self.run_type_explorer(cdist_object)
                    self.run_type_manifest(cdist_object)
                    self.objects_prepared.append(cdist_object)

            objects = self.path.list_objects()

    def stage_run(self):
        """The final (and real) step of deployment"""
        log.debug("Actual run objects")
        # Now do the final steps over the existing objects
        for cdist_object in self.path.list_objects():
            log.debug("Run object: %s", cdist_object)
            self.object_run(cdist_object, mode="gencode")
            self.object_run(cdist_object, mode="code")

    def deploy_to(self):
        """Mimic the old deploy to: Deploy to one host"""
        log.info("Deploying to " + self.target_host)
        time_start = datetime.datetime.now()

        self.stage_prepare()
        self.stage_run()

        time_end = datetime.datetime.now()
        duration = time_end - time_start
        log.info("Finished run of %s in %s seconds", 
            self.target_host,
            duration.total_seconds())

    def deploy_and_cleanup(self):
        """Do what is most often done: deploy & cleanup"""
        self.deploy_to()
        self.cleanup()

def config(args):
    """Configure remote system"""
    process = {}

    time_start = datetime.datetime.now()

    for host in args.host:
        c = Config(host, initial_manifest=args.manifest, home=args.cdist_home, debug=args.debug)
        if args.parallel:
            log.debug("Creating child process for %s", host)
            process[host] = multiprocessing.Process(target=c.deploy_and_cleanup)
            process[host].start()
        else:
            c.deploy_and_cleanup()

    if args.parallel:
        for p in process.keys():
            log.debug("Joining %s", p)
            process[p].join()

    time_end = datetime.datetime.now()
    log.info("Total processing time for %s host(s): %s", len(args.host),
                (time_end - time_start).total_seconds())

def install(args):
    """Install remote system"""
    process = {}

def commandline():
    """Parse command line"""
    # Construct parser others can reuse
    parser = {}
    # Options _all_ parsers have in common
    parser['most'] = argparse.ArgumentParser(add_help=False)
    parser['most'].add_argument('-d', '--debug',
        help='Set log level to debug', action='store_true')

    # Main subcommand parser
    parser['main'] = argparse.ArgumentParser(description='cdist ' + cdist.VERSION)
    parser['main'].add_argument('-V', '--version',
        help='Show version', action='version',
        version='%(prog)s ' + cdist.VERSION)
    parser['sub'] = parser['main'].add_subparsers(title="Commands")

    # Banner
    parser['banner'] = parser['sub'].add_parser('banner', 
        add_help=False)
    parser['banner'].set_defaults(func=cdist.banner.banner)

    # Config and install (common stuff)
    parser['configinstall'] = argparse.ArgumentParser(add_help=False)
    parser['configinstall'].add_argument('host', nargs='+',
        help='one or more hosts to operate on')
    parser['configinstall'].add_argument('-c', '--cdist-home',
         help='Change cdist home (default: .. from bin directory)',
         action='store')
    parser['configinstall'].add_argument('-i', '--initial-manifest', 
         help='Path to a cdist manifest',
         dest='manifest', required=False)
    parser['configinstall'].add_argument('-p', '--parallel',
         help='Operate on multiple hosts in parallel',
         action='store_true', dest='parallel')
    parser['configinstall'].add_argument('-s', '--sequential',
         help='Operate on multiple hosts sequentially (default)',
         action='store_false', dest='parallel')

    # Config
    parser['config'] = parser['sub'].add_parser('config',
        parents=[parser['most'], parser['configinstall']])
    parser['config'].set_defaults(func=config)

    # Install
    parser['install'] = parser['sub'].add_parser('install',
        parents=[parser['most'], parser['configinstall']])
    parser['install'].set_defaults(func=install)

    for p in parser:
        parser[p].epilog = "Get cdist at http://www.nico.schottelius.org/software/cdist/"

    args = parser['main'].parse_args(sys.argv[1:])

    # Most subcommands have --debug, so handle it here
    if 'debug' in args:
        if args.debug:
            logging.root.setLevel(logging.DEBUG)
    log.debug(args)

    args.func(args)


if __name__ == "__main__":
    try:
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

        if re.match(TYPE_PREFIX, os.path.basename(sys.argv[0])):
            cdist_lib = os.environ["__cdist_python_lib"]
            sys.path.insert(0, cdist_lib)
            import cdist.emulator
            cdist.emulator.emulator(sys.argv)
        else:
            cdist_lib = os.path.abspath(os.path.join(os.path.dirname(__file__),
                '../lib'))
            sys.path.insert(0, cdist_lib)

            import cdist
            import cdist.banner
            import cdist.exec
            import cdist.path

            commandline()
    except KeyboardInterrupt:
        sys.exit(0)
    except cdist.Error as e:
        log.error(e)
        sys.exit(1)
