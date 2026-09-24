'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { Show, UserButton } from '@clerk/nextjs';
import { Menu, X } from 'lucide-react';
import { CONNECTOR_CATEGORIES, categoryHref } from './connections/categories';

const links = [
  { href: '/home', label: 'Home' },
  { href: '/projects', label: 'Projects' },
  { href: '/connections', label: 'Connections' },
  { href: '/schedules', label: 'Schedules' },
];

export default function SidebarNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // Without this, tapping a link on mobile would navigate but leave the
  // drawer open, covering the page that just loaded underneath it.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <>
      {/* Fixed positioning takes this out of normal document flow
          entirely, regardless of where it sits in the DOM - so it can
          live here, nested inside the same fragment as the sidebar
          itself, without affecting the parent flex row's layout at all.
          Hidden above lg since the persistent sidebar takes over there. */}
      <div className="fixed inset-x-0 top-0 z-30 flex items-center justify-between border-b border-slate-800 bg-slate-950 px-4 py-3 lg:hidden">
        <Link href="/home" className="flex items-center gap-2">
          <Image src="/logo-icon.png" alt="9Gear Pulse" width={22} height={22} className="rounded" />
          <span className="text-sm font-bold tracking-tight">9Gear Pulse</span>
        </Link>
        <button
          onClick={() => setOpen(true)}
          aria-label="Open menu"
          className="rounded-lg border border-slate-700 p-1.5 text-slate-300 hover:bg-slate-900"
        >
          <Menu className="h-4 w-4" />
        </button>
      </div>

      {/* Only rendered while open, so it never sits in the DOM (and
          never blocks a click) on desktop or when the drawer is closed. */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-64 -translate-x-full flex-col border-r border-slate-800 bg-slate-950 transition-transform duration-200 lg:static lg:z-auto lg:w-64 lg:shrink-0 lg:translate-x-0 ${
          open ? 'translate-x-0' : ''
        }`}
      >
        <div className="flex items-center justify-between px-5 py-4">
          <Link href="/home" className="flex items-center gap-2">
            <Image src="/logo-icon.png" alt="9Gear Pulse" width={24} height={24} className="rounded" />
            <span className="text-sm font-bold tracking-tight">9Gear Pulse</span>
          </Link>
          <button
            onClick={() => setOpen(false)}
            aria-label="Close menu"
            className="rounded-lg p-1 text-slate-500 hover:text-white lg:hidden"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-2">
          {links.map((link) => {
            const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <div key={link.href}>
                <Link
                  href={link.href}
                  className={`block rounded-lg px-3 py-2 text-xs font-semibold ${
                    active ? 'bg-cyan-950 text-cyan-300' : 'text-slate-400 hover:bg-slate-900 hover:text-white'
                  }`}
                >
                  {link.label}
                </Link>

                {/* Tied specifically to the Connections link itself, not
                    to its position in the array - this is what the
                    previous version got wrong: it rendered after the
                    whole .map() finished, so it landed after whichever
                    link happened to be last (Schedules), not actually
                    under Connections at all. */}
                {link.href === '/connections' && pathname.startsWith('/connections') && (
                  <div className="ml-3 mt-1 space-y-0.5 border-l border-slate-800 pl-3">
                    {CONNECTOR_CATEGORIES.map((category) => {
                      const href = categoryHref(category);
                      const catActive = pathname.startsWith(`/connections/${category.slug}`);
                      return (
                        <Link
                          key={category.slug}
                          href={href}
                          className={`block rounded-lg px-2 py-1.5 text-[11px] font-semibold ${
                            catActive ? 'text-cyan-300' : 'text-slate-500 hover:text-white'
                          }`}
                        >
                          {category.label}
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className="border-t border-slate-800 px-5 py-4">
          <Show when="signed-in">
            <UserButton />
          </Show>
          <Show when="signed-out">
            <Link href="/sign-in" className="block rounded-lg bg-cyan-600 px-3 py-2 text-center text-xs font-semibold text-white hover:bg-cyan-500">
              Sign in
            </Link>
          </Show>
        </div>
      </aside>
    </>
  );
}
