import SidebarNav from './sidebar-nav';

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <SidebarNav />
      {/* pt-20 clears the fixed mobile top bar SidebarNav renders below
          lg; lg:pt-8 drops back to a normal top padding once that bar
          is hidden and the persistent sidebar takes over instead.
          mx-auto max-w-6xl preserved from the original layout - the
          pages rendered here (projects_page.tsx, connection-profiles.tsx
          at least) have no width constraint of their own and fully
          depend on this to avoid rendering edge-to-edge on wide screens. */}
      <main className="min-w-0 flex-1 overflow-x-hidden px-6 pb-8 pt-20 lg:px-10 lg:pt-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
