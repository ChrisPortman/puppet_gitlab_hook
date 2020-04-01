#!/usr/bin/python3
import os
import subprocess
import shutil
import argparse
import cherrypy
from cherrypy.process import plugins


class App(object):

    def error(self, msg, status=400):
        cherrypy.log(msg)
        cherrypy.response.status = status
        return {'error': msg}

    def success(self, msg):
        cherrypy.log(msg)
        return msg

    def remove_branch(self, branch):
        code_dir = os.path.join(cherrypy.config.get('base_dir'), branch)
        shutil.rmtree(code_dir, ignore_errors=True)
        if os.path.exists(code_dir):
            msg = 'Failed to remove branch %s from path' % branch
            return self.error(msg, status=500)

        return self.success('Removed branch %s from environment' % branch)

    def update_branch(self, branch):
        git_url = cherrypy.config.get('git_url')
        base_dir = cherrypy.config.get('base_dir')

        code_dir = os.path.join(base_dir, branch)
        tasks = [
            [['checkout', branch], code_dir],
            [['pull'], code_dir],
            [['submodule', 'update', '--init'], code_dir],
        ]

        # Clone the repository if not present
        if not os.path.exists(code_dir):
            tasks.insert(0, [
                ['clone', git_url, branch], base_dir
            ])

        for args, path in tasks:
            cmd = ['/usr/bin/git'] + args
            try:
                subprocess.check_call(cmd, cwd=path)
            except subprocess.CalledProcessError:
                msg = 'Failed to run "%s" on %s' % (' '.join(cmd), path)
                return self.error(msg, status=500)

        return self.success('Refreshed branch %s' % branch)

    def refresh_everything(self):
        gir_url = cherrypy.config.get('git_url')
        base_dir = cherrypy.config.get('base_dir')

        cherrypy.log('Attempting to refresh everything')

        local_branches = set(os.listdir(base_dir))
        # Get list of all remote branches
        cmd = ['/usr/bin/git', 'ls-remote', '--heads', gir_url]
        output = subprocess.check_output(cmd).decode('utf-8')
        remote_branches = []
        for ref in output.split('\n'):
            if ref:
                remote_branches.append(ref.rsplit('/', 1)[1])

        for branch in local_branches.difference(remote_branches):
            self.remove_branch(branch)

        for branch in remote_branches:
            self.update_branch(branch)

    @cherrypy.expose
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def refresh(self):
        data = cherrypy.request.json

        action = data.get('object_kind', 'error')
        if action != 'push':
            return self.error('Missing or unsupported object_kind')

        git_url = cherrypy.config.get('git_url')
        if data.get('repository', {}).get('url', '') != git_url:
            return self.error('Repository missing or not configured')

        _, branch = data.get('ref', '/').rsplit('/', 1)
        if not branch:
            return self.error('missing or invalid ref')

        # Remove deleted branch
        deleted_after = '0000000000000000000000000000000000000000'
        if data.get('after') == deleted_after:
            return self.remove_branch(branch)

        return self.update_branch(branch)


def run():

    parser = argparse.ArgumentParser()
    parser.add_argument('git_url',
                        help='git+ssh URL for Puppet environment repository')
    parser.add_argument('base_dir',
                        help='local filesystem path of Puppet environments')
    parser.add_argument('--interval', default=300,
                        help='how often to refresh all environments')

    args = parser.parse_args()
    cherrypy.config.update({
        'git_url':  args.git_url,
        'base_dir': args.base_dir,
    })

    app = App()

    # Schedule task to refresh all branches every 5 minutes
    task = plugins.BackgroundTask(args.interval, app.refresh_everything)
    task.bus = cherrypy.engine
    task.start()

    cherrypy.quickstart(app, '/api')


if __name__ == '__main__':

    run()

