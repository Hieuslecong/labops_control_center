import Providers from '@/components/layout/providers';
import { Toaster } from '@/components/ui/sonner';
import { fontVariables } from '@/components/themes/font.config';
import { DEFAULT_THEME, THEMES } from '@/components/themes/theme.config';
import ThemeProvider from '@/components/themes/theme-provider';
import { cn } from '@/lib/utils';
import type { Metadata, Viewport } from 'next';
import { cookies } from 'next/headers';
import NextTopLoader from 'nextjs-toploader';
import { NuqsAdapter } from 'nuqs/adapters/next/app';
import '../styles/globals.css';

const META_THEME_COLORS = {
  light: '#ffffff',
  dark: '#09090b'
};

const publicAppUrl = process.env.NEXT_PUBLIC_APP_URL?.trim();
const metadataBase = publicAppUrl ? new URL(publicAppUrl) : undefined;
const socialImage = '/shadcn-dashboard.png';

export const metadata: Metadata = {
  metadataBase,
  title: {
    default: 'AIoT LabOps Control Center',
    template: '%s | AIoT LabOps'
  },
  description:
    'Hệ thống quản trị cụm máy chủ GPU, không gian nghiên cứu, kiểm soát truy cập SSH và giám sát tài nguyên AIoT Lab.',
  openGraph: {
    title: 'AIoT LabOps Control Center',
    description:
      'Hệ thống quản trị cụm máy chủ GPU, không gian nghiên cứu, kiểm soát truy cập SSH và giám sát tài nguyên AIoT Lab.',
    siteName: 'AIoT LabOps',
    type: 'website',
    images: metadataBase
      ? [
          {
            url: socialImage,
            width: 3200,
            height: 1600,
            alt: 'AIoT LabOps Control Center overview page'
          }
        ]
      : undefined
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AIoT LabOps Control Center',
    description:
      'Hệ thống quản trị cụm máy chủ GPU, không gian nghiên cứu, kiểm soát truy cập SSH và giám sát tài nguyên AIoT Lab.',
    images: metadataBase ? [socialImage] : undefined
  }
};

export const viewport: Viewport = {
  themeColor: META_THEME_COLORS.light
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const activeThemeValue = cookieStore.get('active_theme')?.value;
  const isValidTheme = THEMES.some((t) => t.value === activeThemeValue);
  const themeToApply = isValidTheme ? activeThemeValue! : DEFAULT_THEME;
  const enableClerk = Boolean(
    process.env.NEXT_PUBLIC_DEMO_MODE !== 'true' &&
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
    !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.includes('mock') &&
    process.env.CLERK_SECRET_KEY &&
    !process.env.CLERK_SECRET_KEY.includes('mock')
  );

  return (
    <html lang='en' suppressHydrationWarning data-theme={themeToApply}>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              try {
                // Set meta theme color
                if (localStorage.theme === 'dark' || ((!('theme' in localStorage) || localStorage.theme === 'system') && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '${META_THEME_COLORS.dark}')
                }
              } catch (_) {}
            `
          }}
        />
      </head>
      <body
        className={cn(
          'bg-background overflow-x-hidden overscroll-none font-sans antialiased',
          fontVariables
        )}
      >
        <NextTopLoader color='var(--primary)' showSpinner={false} />
        <NuqsAdapter>
          <ThemeProvider
            attribute='class'
            defaultTheme='system'
            enableSystem
            disableTransitionOnChange
            enableColorScheme
          >
            <Providers activeThemeValue={themeToApply} enableClerk={enableClerk}>
              <Toaster />
              {children}
            </Providers>
          </ThemeProvider>
        </NuqsAdapter>
      </body>
    </html>
  );
}
