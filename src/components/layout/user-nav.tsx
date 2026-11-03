'use client';

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
import { useRouter } from 'next/navigation';
import { Icons } from '@/components/icons';

export function UserNav() {
  const router = useRouter();

  const user = {
    fullName: 'Quản trị viên LabOps',
    imageUrl: '',
    emailAddresses: [{ emailAddress: 'admin@labops.local' }]
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant='ghost' className='relative h-8 w-8 rounded-full' />}
      >
        <UserAvatarProfile user={user} />
      </DropdownMenuTrigger>
      <DropdownMenuContent className='w-56' align='end' sideOffset={10}>
        <DropdownMenuGroup>
          <DropdownMenuLabel className='font-normal'>
            <div className='flex flex-col space-y-1'>
              <p className='text-sm leading-none font-medium'>{user.fullName}</p>
              <p className='text-muted-foreground text-xs leading-none'>
                {user.emailAddresses[0].emailAddress}
              </p>
            </div>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => router.push('/dashboard/profile')}>
            <Icons.user className='mr-2 h-4 w-4' /> Hồ sơ
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => router.push('/dashboard/settings')}>
            <Icons.settings className='mr-2 h-4 w-4' /> Cài đặt
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => router.push('/auth/sign-in')}>
            <Icons.logout className='mr-2 h-4 w-4 text-destructive' /> Đăng xuất
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
