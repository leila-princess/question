import { useSession } from 'next-auth/react';
import { useRouter } from 'next/router';
import { useEffect } from 'react';

const withAuth = (WrappedComponent, allowedRoles = []) => {
  return (props) => {
    const { data: session, status } = useSession();
    const router = useRouter();

    useEffect(() => {
      if (status === 'loading') return;
      
      if (!session) {
        router.push('/login?callbackUrl=' + encodeURIComponent(router.asPath));
      } else if (allowedRoles.length > 0 && !allowedRoles.includes(session.user.role)) {
        router.push('/unauthorized');
      }
    }, [session, status, router]);

    if (status === 'loading' || !session) {
      return <div>Loading...</div>;
    }

    return <WrappedComponent {...props} />;
  };
};

export default withAuth;