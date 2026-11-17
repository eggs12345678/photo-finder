// Tiny windowless launcher: PhotoFinder.exe [desktop|stop|serve ...]
// Starts the bundled Python without leaving a console window behind.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "python", "pythonw.exe");
        if (!File.Exists(python))
        {
            python = Path.Combine(root, "python", "python.exe");
        }
        string script = Path.Combine(root, "app", "run.py");
        if (!File.Exists(python) || !File.Exists(script))
        {
            MessageBox.Show(
                "Photo Finder is not installed correctly. Please reinstall it.",
                "Photo Finder",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }

        var start = new ProcessStartInfo(python)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Path.Combine(root, "app"),
        };
        string arguments = Quote(script);
        if (args.Length == 0)
        {
            arguments += " desktop";
        }
        else
        {
            foreach (string arg in args)
            {
                arguments += " " + Quote(arg);
            }
        }
        start.Arguments = arguments;

        try
        {
            Process.Start(start);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "Photo Finder could not start: " + error.Message,
                "Photo Finder",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }
    }
}
