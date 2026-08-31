'use client';

import { usePathname } from 'next/navigation';
import { Show, SignInButton, UserButton } from '@clerk/nextjs';

// The landing page (/) and the dashboard routes each have their own
// sign-in/UserButton UI - showing this corner button there too is a
// redundant second prompt (this is exactly the bug that got fixed on the
// landing page; extending this list is the deliberate fix, not an
// afterthought). Prefix matching, not exact match, so dynamic routes like
// /pipelines/[id] are covered too, not just their static parent path.
// /setup and anything else without its own nav yet still needs this.
const DASHBOARD_PREFIXES = ['/connections', '/pipelines', '/projects', '/schedules'];

export default function AuthCorner() {
  const pathname = usePathname();
  if (pathname === '/' || DASHBOARD_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return null;

  return (
    <div className="fixed top-3 right-4 z-50">
      <Show when="signed-in">
        <UserButton />
      </Show>
      <Show when="signed-out">
        <SignInButton />
      </Show>
    </div>
  );
}
