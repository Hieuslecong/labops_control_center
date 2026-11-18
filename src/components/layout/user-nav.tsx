'use client';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { UserAvatarProfile } from '@/components/user-avatar-profile';
import { useAuthSession } from '@/hooks/use-auth-session';
import { useRouter } from 'next/navigation';
import { Icons } from '@/components/icons';

export function UserNav() {
  const router = useRouter();
  const { user, role, logout } = useAuthSession();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant='ghost'
            className='relative h-8 w-8 rounded-full'
            aria-label='Menu tài khoản người dùng'
          />
        }
      >
        <UserAvatarProfile user={user} />
      </DropdownMenuTrigger>
      <DropdownMenuContent className='w-60' align='end' sideOffset={10}>
        <DropdownMenuGroup>
          <DropdownMenuLabel className='font-normal'>
            <div className='flex flex-col space-y-1.5'>
              <div className='flex items-center justify-between gap-2'>
                <p className='text-sm leading-none font-medium truncate'>{user.fullName}</p>
                <Badge variant='outline' className='text-xs shrink-0'>
                  {role === 'ADMIN' ? 'Admin' : 'Nghiên cứu viên'}
                </Badge>
              </div>
              <p className='text-muted-foreground text-xs leading-none truncate'>
                {user.emailAddresses[0]?.emailAddress || (user.username ? `@${user.username}` : '')}
              </p>
            </div>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          {role === 'ADMIN' ? (
            <>
              <DropdownMenuItem onClick={() => router.push('/dashboard/profile')}>
                <Icons.user className='mr-2 h-4 w-4' /> Hồ sơ
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => router.push('/dashboard/settings')}>
                <Icons.settings className='mr-2 h-4 w-4' /> Cài đặt
              </DropdownMenuItem>
            </>
          ) : (
            <>
              <DropdownMenuItem onClick={() => router.push('/dashboard/researcher')}>
                <Icons.dashboard className='mr-2 h-4 w-4' /> Không gian cá nhân
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => router.push('/dashboard/researcher/connect')}>
                <Icons.key className='mr-2 h-4 w-4' /> Kết nối &amp; Đổi mật khẩu
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => void logout()}>
            <Icons.logout className='mr-2 h-4 w-4 text-destructive' /> Đăng xuất
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
