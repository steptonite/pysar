/*
 * Pysar.app main executable.
 *
 * A shell script as CFBundleExecutable makes the kernel run /bin/bash as the
 * app's main binary, so TCC attributes permissions to bash/Terminal/Python
 * separately instead of one stable "Pysar" identity (live consequence on the
 * 13.07.2026 Air test: Launchpad launches recorded WAVs full of digital zeros
 * while the same code worked from an authorized terminal). A compiled binary
 * inside the bundle is the responsible process; every child (bash → start.sh
 * → bundled Python) inherits that attribution.
 *
 * Compiled by scripts/install_app.sh with the repo paths baked in:
 *   clang -DPYSAR_ROOT='"…"' -DPYSAR_SITE='"…"' -o pysar app_launcher.c
 *
 * It stays alive as the parent (no exec) for the same reason the old script
 * did: start.sh's EXIT trap must fire on quit to stop the whisper server.
 */
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef PYSAR_ROOT
#error "compile with -DPYSAR_ROOT=\"/absolute/repo/path\""
#endif
#ifndef PYSAR_SITE
#error "compile with -DPYSAR_SITE=\"/absolute/site-packages/path\""
#endif

extern char **environ;

static pid_t g_child = 0;

/* Quit/logout lands on the responsible process (us); the whisper-server
 * cleanup lives in start.sh's EXIT trap, so pass the signal down. */
static void forward_signal(int sig) {
    if (g_child > 0)
        kill(g_child, sig);
}

int main(void) {
    /* Resolve Contents/MacOS from our own (real) executable path. */
    char exe[PATH_MAX];
    uint32_t sz = sizeof(exe);
    if (_NSGetExecutablePath(exe, &sz) != 0)
        return 1;
    char realexe[PATH_MAX];
    if (realpath(exe, realexe) == NULL)
        return 1;
    char *dir = dirname(realexe); /* …/Pysar.app/Contents/MacOS */

    char start_sh[PATH_MAX];
    snprintf(start_sh, sizeof(start_sh), "%s/scripts/start.sh", PYSAR_ROOT);
    struct stat st;
    if (stat(start_sh, &st) != 0) {
        char *alert_argv[] = {
            "osascript", "-e",
            "display alert \"Pysar\" message \"Project not found at " PYSAR_ROOT
            ". Re-run make setup there, then make app.\"",
            NULL};
        pid_t pid;
        if (posix_spawn(&pid, "/usr/bin/osascript", NULL, NULL, alert_argv, environ) == 0)
            waitpid(pid, NULL, 0);
        return 1;
    }

    /* Bundled python copy → NSBundle.mainBundle resolves to this .app
     * (Dock/⌘-Tab name + icon); PYSAR_SITE hands it the venv's packages
     * (see scripts/_app_main.py). */
    char py[PATH_MAX];
    snprintf(py, sizeof(py), "%s/Python", dir);
    setenv("PYSAR_PYTHON", py, 1);
    setenv("PYSAR_SITE", PYSAR_SITE, 1);

    /* Same log destination the old script launcher redirected to. */
    const char *home = getenv("HOME");
    char logpath[PATH_MAX];
    snprintf(logpath, sizeof(logpath), "%s/Library/Logs/pysar.log", home ? home : "/tmp");
    int fd = open(logpath, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) {
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        if (fd > STDERR_FILENO)
            close(fd);
    }

    signal(SIGTERM, forward_signal);
    signal(SIGINT, forward_signal);
    signal(SIGHUP, forward_signal);

    char *argv[] = {"/bin/bash", start_sh, NULL};
    if (posix_spawn(&g_child, "/bin/bash", NULL, NULL, argv, environ) != 0)
        return 1;

    int status = 0;
    while (waitpid(g_child, &status, 0) < 0) {
        if (errno != EINTR)
            return 1;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
