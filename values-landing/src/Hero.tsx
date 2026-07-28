import { useState } from 'react'
import {
  Search,
  User,
  Menu,
  X,
  Star,
  Clock,
  Calendar,
  Play,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'

const VIDEO_URL =
  'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260406_094145_4a271a6c-3869-4f1c-8aa7-aeb0cb227994.mp4'

const NAV_LINKS = ['Movies', 'TV Series', "Editor's Pick", 'Interviews', 'User Reviews']

export default function Hero() {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="relative h-screen w-full overflow-hidden bg-black text-white" style={{ height: '100dvh' }}>
      {/* ---------- BACKGROUND VIDEO ---------- */}
      <video
        className="fixed inset-0 h-full w-full object-cover"
        style={{ zIndex: 0 }}
        src={VIDEO_URL}
        autoPlay
        loop
        muted
        playsInline
      />

      {/* ---------- BOTTOM BLUR OVERLAY ---------- */}
      <div
        className="bottom-blur-mask pointer-events-none fixed inset-0 backdrop-blur-xl"
        style={{ zIndex: 1 }}
      />

      {/* ---------- PAGE SHELL ---------- */}
      <div className="relative flex h-full flex-col" style={{ zIndex: 10 }}>
        {/* ---------- NAVBAR ---------- */}
        <nav
          className="relative flex items-center justify-between px-4 py-4 sm:px-6 md:px-12 md:py-6"
          style={{ zIndex: 50 }}
        >
          {/* Logo */}
          <div
            className="animate-blur-fade-up flex h-8 items-center md:h-10"
            style={{ animationDelay: '0ms' }}
          >
            <span
              className="text-xl font-semibold md:text-2xl"
              style={{ letterSpacing: '0.28em' }}
            >
              ROSSA
            </span>
          </div>

          {/* Desktop links */}
          <div className="hidden items-center gap-8 lg:flex">
            {NAV_LINKS.map((link, i) => (
              <a
                key={link}
                href="#"
                className="animate-blur-fade-up text-sm transition-colors hover:text-gray-300"
                style={{ animationDelay: `${100 + i * 50}ms` }}
              >
                {link}
              </a>
            ))}
          </div>

          {/* Right controls */}
          <div className="flex items-center gap-3">
            <button
              className="liquid-glass animate-blur-fade-up hidden items-center gap-2 rounded-full px-4 py-2 text-sm sm:flex md:px-6"
              style={{ animationDelay: '350ms' }}
            >
              <span>Search</span>
              <Search size={18} />
            </button>

            <button
              className="liquid-glass animate-blur-fade-up hidden h-10 w-10 items-center justify-center rounded-full sm:flex"
              style={{ animationDelay: '400ms' }}
              aria-label="Profile"
            >
              <User size={18} />
            </button>

            {/* Hamburger */}
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="liquid-glass animate-blur-fade-up relative flex h-10 w-10 items-center justify-center rounded-full lg:hidden"
              style={{ animationDelay: '350ms' }}
              aria-label="Menu"
            >
              <Menu
                size={18}
                className={`absolute transition-all duration-500 ease-out ${
                  menuOpen ? 'rotate-180 scale-50 opacity-0' : 'rotate-0 scale-100 opacity-100'
                }`}
              />
              <X
                size={18}
                className={`absolute transition-all duration-500 ease-out ${
                  menuOpen ? 'rotate-0 scale-100 opacity-100' : 'rotate-180 scale-50 opacity-0'
                }`}
              />
            </button>
          </div>
        </nav>

        {/* ---------- MOBILE MENU ---------- */}
        <div
          className={`absolute left-0 right-0 top-[72px] border-b border-t border-gray-800 bg-gray-900/95 shadow-2xl backdrop-blur-lg transition-all duration-500 ease-out lg:hidden ${
            menuOpen
              ? 'translate-y-0 opacity-100'
              : 'pointer-events-none -translate-y-4 opacity-0'
          }`}
          style={{ zIndex: 40 }}
        >
          <div className="flex flex-col px-4 py-3 sm:px-6">
            {NAV_LINKS.map((link, i) => (
              <a
                key={link}
                href="#"
                onClick={() => setMenuOpen(false)}
                className={`rounded-lg px-3 py-3 text-sm transition-all duration-500 ease-out hover:bg-gray-800/50 ${
                  menuOpen ? 'translate-x-0 opacity-100' : '-translate-x-4 opacity-0'
                }`}
                style={{ transitionDelay: menuOpen ? `${i * 50}ms` : '0ms' }}
              >
                {link}
              </a>
            ))}
          </div>

          {/* Sub-sm extras */}
          <div className="flex items-center gap-3 border-t border-gray-800 px-4 py-4 sm:hidden">
            <button className="liquid-glass flex flex-1 items-center justify-center gap-2 rounded-full px-4 py-2.5 text-sm">
              <span>Search</span>
              <Search size={18} />
            </button>
            <button
              className="liquid-glass flex h-10 w-10 items-center justify-center rounded-full"
              aria-label="Profile"
            >
              <User size={18} />
            </button>
          </div>
        </div>

        {/* ---------- HERO CONTENT ---------- */}
        <div
          className="flex flex-1 flex-col justify-end px-4 pb-8 sm:px-6 md:px-12 md:pb-16"
          style={{ zIndex: 10 }}
        >
          <div className="flex flex-col items-end gap-8 md:flex-row">
            {/* Left */}
            <div className="w-full flex-1">
              {/* Metadata */}
              <div
                className="animate-blur-fade-up mb-6 flex flex-wrap items-center gap-3 text-xs sm:gap-6 sm:text-sm md:mb-8"
                style={{ animationDelay: '300ms' }}
              >
                <span className="flex items-center gap-2">
                  <Star size={16} className="fill-white sm:h-5 sm:w-5" />
                  <span className="font-medium">8.7/10 IMDB</span>
                </span>
                <span className="flex items-center gap-2">
                  <Clock size={16} />
                  <span>132 min</span>
                </span>
                <span className="flex items-center gap-2">
                  <Calendar size={16} />
                  <span>April, 2025</span>
                </span>
              </div>

              {/* Title */}
              <h1
                className="animate-blur-fade-up mb-4 text-3xl font-normal sm:text-5xl md:mb-6 md:text-6xl lg:text-7xl"
                style={{ letterSpacing: '-0.04em', animationDelay: '400ms' }}
              >
                Step Through. Work Smarter.
              </h1>

              {/* Description */}
              <p
                className="animate-blur-fade-up mb-6 max-w-2xl text-base text-gray-400 sm:text-lg md:mb-12 md:text-xl"
                style={{ animationDelay: '500ms' }}
              >
                A voyage through forgotten realms, where past and future intertwine.
              </p>

              {/* CTAs */}
              <div className="flex flex-wrap items-center gap-3 sm:gap-4">
                <button
                  className="animate-blur-fade-up flex items-center gap-2 rounded-full bg-white px-6 py-2.5 font-medium text-black transition-colors hover:bg-gray-200 sm:px-8 sm:py-3"
                  style={{ animationDelay: '600ms' }}
                >
                  <Play size={18} className="fill-black" />
                  <span>Watch Now</span>
                </button>

                <button
                  className="liquid-glass animate-blur-fade-up rounded-full px-6 py-2.5 font-medium sm:px-8 sm:py-3"
                  style={{ animationDelay: '700ms' }}
                >
                  Learn More
                </button>
              </div>
            </div>

            {/* Right — arrows */}
            <div className="flex w-full items-center gap-3 sm:gap-4 md:w-auto md:justify-end">
              <button
                className="liquid-glass animate-blur-fade-up flex items-center gap-2 rounded-full px-4 py-2.5 text-sm sm:px-6 sm:py-3"
                style={{ animationDelay: '800ms' }}
              >
                <ChevronLeft size={18} />
                <span>Previous</span>
              </button>

              <button
                className="liquid-glass animate-blur-fade-up flex items-center gap-2 rounded-full px-4 py-2.5 text-sm sm:px-6 sm:py-3"
                style={{ animationDelay: '900ms' }}
              >
                <span>Next</span>
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
