import { clerkMiddleware } from '@clerk/nextjs/server';

// Stage 1: Clerk is wired up and sign-in/sign-up work, but no routes are
// protected yet - /setup and everything else keep working exactly as
// they do today, still on the actor_id pattern. Protecting real routes
// (and replacing actor_id with verified sessions on the backend) is a
// deliberate stage 2, not bundled in here.
export default clerkMiddleware();

export const config = {
  matcher: [
    // Skip Next.js internals and static files, unless referenced via a search param
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)',
    // Always run for Clerk's own frontend API routes
    '/__clerk/(.*)',
  ],
};
