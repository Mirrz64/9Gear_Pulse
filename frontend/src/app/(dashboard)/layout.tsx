import Link from 'next/link';
import Image from 'next/image';
import { Show, UserButton } from '@clerk/nextjs';

const links = [
  { href: '/projects', label: 'Projects' },
  { href: '/connections', label: 'Connections' },
  { href: '/schedules', label: 'Schedules' },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-8 py-4">
          <div className="flex items-center gap-8">
            <Link href="/" className="flex items-center gap-2">
              <Image src="/logo-icon.png" alt="9Gear Pulse" width={24} height={24} className="rounded" />
              <span className="text-sm font-bold tracking-tight">9Gear Pulse</span>
            </Link>
            <nav className="flex items-center gap-1">
              {links.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-400 hover:bg-slate-900 hover:text-white"
                >
                  {link.label}
                </Link>
              ))}
            </nav>
          </div>
          <Show when="signed-in">
            <UserButton />
          </Show>
          <Show when="signed-out">
            <Link href="/sign-in" className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-cyan-500">
              Sign in
            </Link>
          </Show>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-8 py-8">{children}</main>
    </div>
  );
}
