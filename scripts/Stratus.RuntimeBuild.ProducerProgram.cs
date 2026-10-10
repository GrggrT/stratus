// UNQUALIFIED standalone disposable-producer entrypoint. Compile separately only
// in an explicitly qualified build/execution window. The shared invocation owns
// the original inherited ABI, identity, clock, channel, context and work phases;
// this thin entry supplies no replacement deadlines, owners or approval state.
using System;
using System.Threading;

namespace Stratus.RuntimeBuild.ProducerEntry {
    internal static class Program {
        private static Stratus.RuntimeBuild.ProducerInvocation invocation;

        private static int Main(string[] args) {
            try {
                invocation = Stratus.RuntimeBuild.ProducerInvocation.PrepareFromInheritedEntry(args);
                invocation.Start();
                while (true) {
                    invocation.Poll();
                    if (invocation.PollTerminalExit()) return 0;
                    Thread.Sleep(10);
                    invocation.AssertDeadline();
                }
            } catch (Exception) {
                if (invocation != null) invocation.RecordFailure();
                return 205;
            }
            // Zero is only the no-argument invocation's actual terminal decision,
            // never build acceptance. No competing cleanup or W-death wait.
            // Fault 205 retains unresolved original state until physical P death.
        }
    }
}
