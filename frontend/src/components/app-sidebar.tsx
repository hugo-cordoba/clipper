import * as React from "react"
import { ChevronsUpDown, Film, History, LayoutGrid, LogOut, Scissors, Settings, UserRound } from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader,
  SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarRail, useSidebar,
} from "@/components/ui/sidebar"

type User = { name: string; email: string; avatar?: string }

// Sustituye por los datos reales cuando haya login
const USER: User = { name: "Tu cuenta", email: "tu@correo.com" }

const NAV = [
  { title: "Generador de clips", icon: LayoutGrid, active: true },
  { title: "Historial", icon: History, soon: true },
  { title: "Biblioteca", icon: Film, soon: true },
  { title: "Ajustes", icon: Settings, soon: true },
]

function Brand() {
  return (
    <div className="flex h-10 items-center gap-2.5 px-1">
      <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-linear-to-br from-orange-400 to-orange-600 text-white shadow-sm">
        <Scissors className="size-4" />
      </div>
      <span className="text-[15px] font-bold tracking-[0.14em] group-data-[collapsible=icon]:hidden">CLIPPING</span>
    </div>
  )
}

function NavUser({ user }: { user: User }) {
  const { isMobile } = useSidebar()
  const initials = user.name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase()
  const avatar = (
    <Avatar className="size-8 rounded-lg">
      {user.avatar && <AvatarImage src={user.avatar} alt={user.name} />}
      <AvatarFallback className="rounded-lg bg-primary/15 font-medium text-orange-800">{initials}</AvatarFallback>
    </Avatar>
  )
  const info = (
    <div className="grid flex-1 text-left text-sm leading-tight">
      <span className="truncate font-medium">{user.name}</span>
      <span className="truncate text-xs text-muted-foreground">{user.email}</span>
    </div>
  )
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
              {avatar}
              {info}
              <ChevronsUpDown className="ml-auto size-4 text-muted-foreground" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg"
            side={isMobile ? "bottom" : "right"} align="end" sideOffset={8}
          >
            <DropdownMenuLabel className="flex items-center gap-2 p-1.5 font-normal">{avatar}{info}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem><UserRound /> Mi cuenta</DropdownMenuItem>
              <DropdownMenuItem><Settings /> Ajustes</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive focus:text-destructive [&_svg]:text-destructive">
              <LogOut /> Cerrar sesión
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

export function AppSidebar({ user = USER, ...props }: React.ComponentProps<typeof Sidebar> & { user?: User }) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader className="h-14 justify-center border-b">
        <Brand />
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton
                    tooltip={item.soon ? `${item.title} (pronto)` : item.title}
                    isActive={item.active}
                    disabled={item.soon}
                    className="relative overflow-visible before:absolute before:-left-2 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-full before:bg-primary before:opacity-0 data-[active=true]:bg-primary/10 data-[active=true]:font-semibold data-[active=true]:before:opacity-100"
                  >
                    <item.icon />
                    <span>{item.title}</span>
                    {item.soon && (
                      <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground group-data-[collapsible=icon]:hidden">
                        Pronto
                      </span>
                    )}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t">
        <NavUser user={user} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}