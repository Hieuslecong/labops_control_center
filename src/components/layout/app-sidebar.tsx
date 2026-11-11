'use client';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
  useSidebar
} from '@/components/ui/sidebar';
import { UserAvatarProfile } from '@/components/user-avatar-profile';
import { useFilteredNavGroups } from '@/hooks/use-nav';
import { useAuthSession } from '@/hooks/use-auth-session';
import type { NavItem } from '@/types';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Icons } from '../icons';
import { OrgSwitcher } from '../org-switcher';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

export default function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const filteredGroups = useFilteredNavGroups();
  const { user, role, actualRole, isPreviewMode, setPreviewRole } = useAuthSession();

  return (
    <Sidebar collapsible='icon'>
      <SidebarHeader className='group-data-[collapsible=icon]:pt-4'>
        <OrgSwitcher />
        {isPreviewMode && (
          <div className='mx-2 my-1.5 p-2 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-md text-[11px] text-amber-800 dark:text-amber-200 group-data-[collapsible=icon]:hidden'>
            <div className='flex items-center justify-between font-semibold'>
              <span>Chế độ Xem thử</span>
              <Button
                size='sm'
                variant='ghost'
                className='h-5 px-1.5 text-[10px] text-amber-900 dark:text-amber-100 hover:bg-amber-200/50'
                onClick={() => setPreviewRole(null)}
              >
                Thoát
              </Button>
            </div>
            <p className='mt-0.5 text-[10px] text-amber-700/80 dark:text-amber-300/70'>
              Đang mô phỏng giao diện Nghiên cứu viên.
            </p>
          </div>
        )}
      </SidebarHeader>
      <SidebarContent className='overflow-x-hidden'>
        {filteredGroups.map((group) => (
          <SidebarGroup key={group.label || 'ungrouped'} className='py-0'>
            {group.label && <SidebarGroupLabel>{group.label}</SidebarGroupLabel>}
            <SidebarMenu>
              {group.items.map((item) => (
                <NavMainItem key={item.title} item={item} pathname={pathname} />
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <SidebarMenuButton
                    size='lg'
                    className='data-popup-open:bg-sidebar-accent data-popup-open:text-sidebar-accent-foreground'
                  />
                }
              >
                <UserAvatarProfile className='h-8 w-8 rounded-lg' showInfo user={user} />
                <Icons.chevronsDown className='ml-auto size-4' />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                className='w-(--anchor-width) min-w-56 rounded-lg'
                side='bottom'
                align='end'
                sideOffset={4}
              >
                <DropdownMenuGroup>
                  <DropdownMenuLabel className='p-0 font-normal'>
                    <div className='px-1 py-1.5 flex flex-col gap-1.5'>
                      <UserAvatarProfile
                        className='h-8 w-8 rounded-lg'
                        showInfo
                        user={user}
                      />
                      <div className='flex items-center gap-1.5 px-1'>
                        <Badge
                          variant='outline'
                          className={`text-[10px] font-medium ${
                            role === 'ADMIN'
                              ? 'bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30'
                              : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30'
                          }`}
                        >
                          {role === 'ADMIN' ? 'Quản trị viên' : 'Nghiên cứu viên'}
                        </Badge>
                        {isPreviewMode && (
                          <Badge variant='outline' className='text-[10px] bg-amber-500/10 text-amber-700 border-amber-500/30'>
                            Xem thử
                          </Badge>
                        )}
                      </div>
                    </div>
                  </DropdownMenuLabel>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />

                {actualRole === 'ADMIN' && (
                  <>
                    <DropdownMenuGroup>
                      <DropdownMenuItem
                        onClick={() => {
                          if (isPreviewMode) {
                            setPreviewRole(null);
                            router.push('/dashboard/overview');
                          } else {
                            setPreviewRole('RESEARCHER');
                            router.push('/dashboard/researcher');
                          }
                        }}
                      >
                        <Icons.user className='mr-2 h-4 w-4' />
                        {isPreviewMode ? 'Quay lại chế độ Quản trị' : 'Xem thử giao diện Nghiên cứu viên'}
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                    <DropdownMenuSeparator />
                  </>
                )}

                <DropdownMenuGroup>
                  {role === 'ADMIN' ? (
                    <>
                      <DropdownMenuItem onClick={() => router.push('/dashboard/settings')}>
                        <Icons.settings className='mr-2 h-4 w-4' />
                        Cài đặt hệ thống
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => router.push('/dashboard/users')}>
                        <Icons.teams className='mr-2 h-4 w-4' />
                        Quản trị thành viên
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => router.push('/dashboard/audit-logs')}>
                        <Icons.audit className='mr-2 h-4 w-4' />
                        Nhật ký kiểm toán
                      </DropdownMenuItem>
                    </>
                  ) : (
                    <>
                      <DropdownMenuItem onClick={() => router.push('/dashboard/researcher/connect')}>
                        <Icons.key className='mr-2 h-4 w-4' />
                        Khóa SSH & Kết nối
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => router.push('/dashboard/researcher/workspace')}>
                        <Icons.database className='mr-2 h-4 w-4' />
                        Workspace của tôi
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

function isActivePath(pathname: string, url: string) {
  return pathname === url || (url !== '/' && pathname.startsWith(`${url}/`));
}

function NavMainItem({ item, pathname }: { item: NavItem; pathname: string }) {
  const { state, isMobile } = useSidebar();
  const Icon = item.icon ? Icons[item.icon] : Icons.logo;
  const children = item.items ?? [];

  if (children.length === 0) {
    return (
      <SidebarMenuItem>
        <SidebarMenuButton
          render={<Link href={item.url} aria-label={item.title} />}
          tooltip={item.title}
          isActive={isActivePath(pathname, item.url)}
        >
          {Icon && <Icon className='size-4' />}
          <span>{item.title}</span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    );
  }

  const hasActiveChild = children.some((child) => isActivePath(pathname, child.url));

  if (state === 'collapsed' && !isMobile) {
    return (
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton
                tooltip={item.title}
                isActive={hasActiveChild}
                className='data-popup-open:bg-sidebar-accent data-popup-open:text-sidebar-accent-foreground'
              />
            }
          >
            {Icon && <Icon className='size-4' />}
            <span>{item.title}</span>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side='right'
            align='start'
            sideOffset={8}
            className='w-auto min-w-48'
          >
            <DropdownMenuGroup>
              <DropdownMenuLabel>{item.title}</DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              {children.map((child) => {
                const ChildIcon = child.icon ? Icons[child.icon] : null;
                const active = isActivePath(pathname, child.url);
                return (
                  <DropdownMenuItem
                    key={child.title}
                    render={
                      <Link
                        href={child.url}
                        aria-label={child.title}
                        aria-current={active ? 'page' : undefined}
                      />
                    }
                    className={active ? 'bg-accent text-accent-foreground font-medium' : undefined}
                  >
                    {ChildIcon && <ChildIcon />}
                    {child.title}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    );
  }

  return (
    <Collapsible defaultOpen={item.isActive || hasActiveChild} render={<SidebarMenuItem />}>
      <CollapsibleTrigger
        render={
          <SidebarMenuButton
            tooltip={item.title}
            isActive={pathname === item.url}
            className='group/collapsible'
          />
        }
      >
        {item.icon && <Icon className='size-4' />}
        <span>{item.title}</span>
        <Icons.chevronRight className='ml-auto transition-transform duration-200 group-data-panel-open/collapsible:rotate-90' />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <SidebarMenuSub>
          {children.map((child) => (
            <SidebarMenuSubItem key={child.title}>
              <SidebarMenuSubButton
                render={<Link href={child.url} aria-label={child.title} />}
                isActive={pathname === child.url}
              >
                <span>{child.title}</span>
              </SidebarMenuSubButton>
            </SidebarMenuSubItem>
          ))}
        </SidebarMenuSub>
      </CollapsibleContent>
    </Collapsible>
  );
}
